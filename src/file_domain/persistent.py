"""Account-owned persistent paths backed by immutable Web file revisions."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from uuid6 import uuid7

from file_runtime.output import OutputPublisher, PublishedOutput
from persistence.uow import UnitOfWork, retryable_transaction
from storage.tenant_file_store import TenantFileStore
from workspace.file_scope import RunFileScope

from .approvals import ApprovalNotGranted, FileActionApprovalService


class DestinationChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class PersistentFileDestination:
    destination_id: UUID
    account_id: UUID
    logical_path: str
    current_revision: int
    current_file_id: UUID
    current_sha256: str
    last_operation_id: str


@dataclass(frozen=True)
class PersistentFileResult:
    status: str
    logical_path: str
    revision: int
    file_id: UUID | None
    sha256: str
    operation_id: str
    approval_id: UUID | None = None
    deduplicated: bool = False


class PersistentFileRepository:
    """Account-scoped destination pointer and immutable revision ledger."""

    def __init__(self, database: object) -> None:
        self.database = database

    def get(self, account_id: UUID, logical_path: str) -> PersistentFileDestination | None:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT * FROM persistent_file_destinations WHERE account_id=%s "
                "AND logical_path=%s", (account_id, logical_path),
            ).fetchone()
        return self._model(row) if row else None

    @retryable_transaction
    def create(
        self, account_id: UUID, logical_path: str, operation_id: str,
        output: PublishedOutput,
    ) -> PersistentFileResult:
        with UnitOfWork(self.database) as uow:
            existing = uow.execute(
                "SELECT * FROM persistent_file_destinations WHERE account_id=%s "
                "AND logical_path=%s FOR UPDATE", (account_id, logical_path),
            ).fetchone()
            if existing is not None:
                if (existing["last_operation_id"] == operation_id
                        and existing["current_sha256"] == output.sha256):
                    return self._result(existing, operation_id, deduplicated=True)
                raise DestinationChanged("persistent destination was concurrently created")
            destination_id = uuid7()
            row = uow.execute(
                "INSERT INTO persistent_file_destinations(destination_id,account_id,"
                "logical_path,current_revision,current_file_id,current_sha256,last_operation_id) "
                "VALUES (%s,%s,%s,1,%s,%s,%s) RETURNING *",
                (destination_id, account_id, logical_path, output.file_id, output.sha256,
                 operation_id),
            ).fetchone()
            uow.execute(
                "INSERT INTO persistent_file_revisions(account_id,destination_id,revision,"
                "file_id,sha256,operation_id) VALUES (%s,%s,1,%s,%s,%s)",
                (account_id, destination_id, output.file_id, output.sha256, operation_id),
            )
            return self._result(row, operation_id)

    @retryable_transaction
    def advance(
        self, account_id: UUID, destination_id: UUID, intent: dict[str, Any],
        operation_id: str, output: PublishedOutput,
    ) -> PersistentFileResult:
        expected = int(intent["expected_revision"])
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "UPDATE persistent_file_destinations SET current_revision=current_revision+1,"
                "current_file_id=%s,current_sha256=%s,last_operation_id=%s,updated_at=now() "
                "WHERE account_id=%s AND destination_id=%s AND current_revision=%s "
                "AND current_sha256=%s RETURNING *",
                (output.file_id, output.sha256, operation_id, account_id, destination_id,
                 expected, intent["expected_sha256"]),
            ).fetchone()
            if row is None:
                current = uow.execute(
                    "SELECT * FROM persistent_file_destinations WHERE account_id=%s "
                    "AND destination_id=%s", (account_id, destination_id),
                ).fetchone()
                if (current and current["last_operation_id"] == operation_id
                        and current["current_sha256"] == output.sha256):
                    return self._result(current, operation_id, deduplicated=True)
                raise DestinationChanged("persistent destination CAS failed")
            uow.execute(
                "INSERT INTO persistent_file_revisions(account_id,destination_id,revision,"
                "file_id,sha256,operation_id) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (destination_id,operation_id) DO NOTHING",
                (account_id, destination_id, expected + 1, output.file_id,
                 output.sha256, operation_id),
            )
            return self._result(row, operation_id)

    @staticmethod
    def _model(row: Any) -> PersistentFileDestination:
        return PersistentFileDestination(
            row["destination_id"], row["account_id"], str(row["logical_path"]),
            int(row["current_revision"]), row["current_file_id"],
            str(row["current_sha256"]), str(row["last_operation_id"]),
        )

    @staticmethod
    def _result(row: Any, operation_id: str, *, deduplicated: bool = False):
        return PersistentFileResult(
            "completed", str(row["logical_path"]), int(row["current_revision"]),
            row["current_file_id"], str(row["current_sha256"]), operation_id,
            deduplicated=deduplicated,
        )


class PersistentWebFileService:
    TOOL_NAME = "save_persistent_file"

    def __init__(
        self, database: object, store: TenantFileStore,
        approvals: FileActionApprovalService | None = None,
    ) -> None:
        self.database = database
        self.store = store
        self.publisher = OutputPublisher(database, store)
        self.approvals = approvals or FileActionApprovalService(database)
        self.destinations = PersistentFileRepository(database)

    def save(
        self, account_id: UUID, run_id: UUID, operation_id: str,
        logical_path: str, source_file_id: UUID,
    ) -> PersistentFileResult:
        path = self._path(logical_path)
        source = self._source(account_id, run_id, source_file_id)
        current = self.destinations.get(account_id, path)
        if current is None:
            output = self._publish_revision(
                run_id, operation_id, path, source, source_file_id
            )
            return self.destinations.create(account_id, path, operation_id, output)
        if (current.last_operation_id == operation_id
                and current.current_sha256 == source["sha256"]):
            return self._result(current, operation_id, deduplicated=True)
        intent = {
            "logical_path": path,
            "source_file_id": str(source_file_id),
            "expected_revision": current.current_revision,
            "expected_sha256": current.current_sha256,
            "new_sha256": str(source["sha256"]),
        }
        arguments_hash = self.arguments_hash(intent)
        approval = self.approvals.request(
            account_id, source["conversation_id"], run_id, operation_id,
            self.TOOL_NAME, f"Overwrite persistent file {path}", arguments_hash,
            intent=intent,
        )
        return PersistentFileResult(
            "approval_required", path, current.current_revision,
            current.current_file_id, current.current_sha256, operation_id,
            approval.approval_id,
        )

    def execute_approved_overwrite(
        self, account_id: UUID, run_id: UUID, operation_id: str,
        *, execution_id: str, fencing_token: int,
    ) -> PersistentFileResult:
        approval = self._approval(account_id, run_id, operation_id)
        intent = dict(approval["intent"])
        arguments_hash = self.arguments_hash(intent)
        if arguments_hash != approval["arguments_hash"]:
            raise ApprovalNotGranted()
        self.approvals.consume(
            account_id, run_id, operation_id, self.TOOL_NAME, arguments_hash,
            execution_id=execution_id, fencing_token=fencing_token,
        )
        path = self._path(str(intent["logical_path"]))
        current = self.destinations.get(account_id, path)
        if current is None:
            raise DestinationChanged("persistent destination is unavailable")
        if (current.last_operation_id == operation_id
                and current.current_sha256 == intent["new_sha256"]):
            return self._result(current, operation_id, deduplicated=True)
        if (current.current_revision != int(intent["expected_revision"])
                or current.current_sha256 != intent["expected_sha256"]):
            raise DestinationChanged("persistent destination changed after approval")
        source = self._source(account_id, run_id, UUID(str(intent["source_file_id"])))
        if source["sha256"] != intent["new_sha256"]:
            raise DestinationChanged("approved source content changed")
        output = self._publish_revision(
            run_id, operation_id, path, source, current.current_file_id
        )
        return self.destinations.advance(
            account_id, current.destination_id, intent, operation_id, output
        )

    def reconcile_approved_overwrite(
        self, account_id: UUID, run_id: UUID, operation_id: str,
    ) -> tuple[str, PersistentFileResult | None]:
        approval = self._approval(account_id, run_id, operation_id)
        intent = dict(approval["intent"])
        current = self.destinations.get(account_id, self._path(intent["logical_path"]))
        if current is None:
            return "conflict", None
        if (current.last_operation_id == operation_id
                and current.current_sha256 == intent["new_sha256"]
                and current.current_revision == int(intent["expected_revision"]) + 1):
            return "confirmed_completed", self._result(
                current, operation_id, deduplicated=True
            )
        if (current.current_revision == int(intent["expected_revision"])
                and current.current_sha256 == intent["expected_sha256"]):
            return "confirmed_not_executed", None
        return "conflict", None

    @staticmethod
    def arguments_hash(intent: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(
            intent, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()

    @staticmethod
    def _path(value: str) -> str:
        candidate = PurePosixPath(value.strip())
        if (not value.strip() or len(value.strip()) > 500 or candidate.is_absolute()
                or any(part in {"", ".", ".."} for part in candidate.parts)
                or "\\" in value or "\x00" in value):
            raise ValueError("invalid persistent logical path")
        return candidate.as_posix()

    def _source(self, account_id: UUID, run_id: UUID, file_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT sf.*,r.conversation_id AS run_conversation_id FROM stored_files sf "
                "JOIN run_files rf ON rf.file_id=sf.file_id JOIN runs r ON r.run_id=rf.run_id "
                "WHERE sf.account_id=%s AND rf.run_id=%s AND sf.file_id=%s "
                "AND sf.status='ready' AND r.account_id=%s",
                (account_id, run_id, file_id, account_id),
            ).fetchone()
        if row is None or row["conversation_id"] != row["run_conversation_id"]:
            raise LookupError("owned Run file source is unavailable")
        return dict(row)

    def _approval(self, account_id: UUID, run_id: UUID, operation_id: str) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT * FROM file_action_approvals WHERE account_id=%s AND run_id=%s "
                "AND operation_id=%s AND tool_name=%s",
                (account_id, run_id, operation_id, self.TOOL_NAME),
            ).fetchone()
        if row is None:
            raise ApprovalNotGranted()
        return dict(row)

    def _publish_revision(
        self, run_id: UUID, operation_id: str, path: str,
        source: dict[str, Any], parent_file_id: UUID,
    ) -> PublishedOutput:
        suffix = Path(path).suffix
        path_key = hashlib.sha256(path.encode()).hexdigest()[:12]
        operation_key = hashlib.sha256(operation_id.encode()).hexdigest()[:12]
        name = f"persistent-{path_key}-{operation_key}{suffix}"
        with TemporaryDirectory(prefix="hpagent-persistent-") as root:
            base = Path(root)
            inputs, scratch, outputs = base / "inputs", base / "scratch", base / "outputs"
            for directory in (inputs, scratch, outputs):
                directory.mkdir()
            scope = RunFileScope(run_id, inputs, scratch, outputs, ())
            replay = self.publisher.replay(
                scope, operation_id, name, parent_file_id
            )
            if replay is not None:
                return replay
            with self.store.open(source["storage_key"]) as reader, (
                outputs / name
            ).open("wb") as writer:
                shutil.copyfileobj(reader, writer, length=64 * 1024)
            return self.publisher.publish(
                scope, operation_id, name, source["content_type"], parent_file_id,
                encoding=source["encoding"] or "binary",
            )

    @staticmethod
    def _result(
        destination: PersistentFileDestination, operation_id: str,
        *, deduplicated: bool = False,
    ) -> PersistentFileResult:
        return PersistentFileResult(
            "completed", destination.logical_path, destination.current_revision,
            destination.current_file_id, destination.current_sha256, operation_id,
            deduplicated=deduplicated,
        )
