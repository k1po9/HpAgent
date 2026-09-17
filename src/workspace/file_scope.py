"""Per-Run file execution view, separate from the Account Git repository."""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

from persistence.uow import UnitOfWork
from storage.tenant_file_store import FileStoreError, TenantFileReader, TenantFileStore


class RunFileScopeUnavailable(RuntimeError):
    code = "sandbox_unavailable"


@dataclass(frozen=True)
class RunFileInput:
    file_id: UUID
    logical_name: str
    size_bytes: int
    encoding: str
    content_type: str | None = None
    sha256: str | None = None
    direction: str = "input"


@dataclass(frozen=True)
class RunFileScope:
    run_id: UUID
    inputs_root: Path
    scratch_root: Path
    outputs_root: Path
    inputs: tuple[RunFileInput, ...]
    outputs: tuple[RunFileInput, ...] = ()

    def model_manifest(self) -> list[dict[str, Any]]:
        manifest = []
        for item in (*self.inputs, *self.outputs):
            record = {
                "file": item.logical_name,
                "file_id_suffix": str(item.file_id)[-8:],
                "size_bytes": item.size_bytes,
                "encoding": item.encoding,
            }
            if item.direction == "output":
                record["direction"] = "output"
            manifest.append(record)
        return manifest


class RunFileWorkspace:
    """Materialize authoritative ``run_files`` as immutable local inputs."""

    def __init__(
        self, database: object, store: TenantFileStore | TenantFileReader,
        execution_root: Path | str,
    ) -> None:
        self.database = database
        self.store = store
        self.execution_root = Path(execution_root).resolve()
        self.execution_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.execution_root.chmod(0o700)
        if self.execution_root == store.root or self.execution_root.is_relative_to(store.root):
            raise RunFileScopeUnavailable("execution root must not overlap file store")
        if store.root.is_relative_to(self.execution_root):
            raise RunFileScopeUnavailable("file store must not overlap execution root")

    def load_rows(self, account_id: UUID, run_id: UUID) -> list[dict[str, Any]]:
        with UnitOfWork(self.database) as uow:
            owned = uow.execute(
                "SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s",
                (account_id, run_id),
            ).fetchone()
            if owned is None:
                raise RunFileScopeUnavailable("authoritative Run scope is unavailable")
            return list(uow.execute(
                "SELECT rf.file_id,rf.logical_name,rf.direction,sf.storage_key,sf.size_bytes,sf.encoding,"
                "sf.content_type,sf.sha256 FROM run_files rf JOIN stored_files sf "
                "ON sf.account_id=rf.account_id "
                "AND sf.conversation_id=rf.conversation_id AND sf.file_id=rf.file_id "
                "WHERE rf.account_id=%s AND rf.run_id=%s AND sf.status='ready' "
                "ORDER BY rf.direction,rf.logical_name",
                (account_id, run_id),
            ).fetchall())

    @contextmanager
    def prepare(self, account_id: UUID, run_id: UUID) -> Iterator[RunFileScope]:
        rows = self.load_rows(account_id, run_id)
        with self.prepare_rows(run_id, rows) as scope:
            yield scope

    @contextmanager
    def prepare_rows(
        self, run_id: UUID, rows: list[dict[str, Any]],
    ) -> Iterator[RunFileScope]:
        run_root = self.execution_root / str(run_id)
        if not run_root.is_relative_to(self.execution_root):
            raise RunFileScopeUnavailable("Run root escapes execution root")
        if run_root.exists():
            # A killed Activity cannot execute the context-manager cleanup. The
            # replacement attempt owns the same authoritative Run and rebuilds
            # the scope solely from immutable TenantFileStore objects.
            self._remove_stale_run_root(run_root)
        inputs_root = run_root / "inputs"
        scratch_root = run_root / "scratch"
        outputs_root = run_root / "outputs"
        materialized: list[RunFileInput] = []
        materialized_outputs: list[RunFileInput] = []
        try:
            for directory in (run_root, inputs_root, scratch_root, outputs_root):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                directory.chmod(0o700)
            seen: dict[str, set[str]] = {"input": set(), "output": set()}
            for row in rows:
                direction = str(row.get("direction") or "input")
                if direction not in seen:
                    raise RunFileScopeUnavailable("invalid Run file direction")
                logical_name = self._logical_name(str(row["logical_name"]))
                folded = logical_name.casefold()
                if folded in seen[direction]:
                    raise RunFileScopeUnavailable("duplicate logical Run file name")
                seen[direction].add(folded)
                destination_root = inputs_root if direction == "input" else outputs_root
                target = destination_root / logical_name
                temporary = destination_root / f".{logical_name}.part"
                with self.store.open(str(row["storage_key"])) as source:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(temporary, flags, 0o600)
                    try:
                        with os.fdopen(fd, "wb") as destination:
                            fd = -1
                            shutil.copyfileobj(source, destination, length=64 * 1024)
                            destination.flush()
                            os.fsync(destination.fileno())
                    finally:
                        if fd >= 0:
                            os.close(fd)
                os.replace(temporary, target)
                target.chmod(0o400)
                item = RunFileInput(
                    file_id=row["file_id"], logical_name=logical_name,
                    size_bytes=int(row["size_bytes"]), encoding=str(row["encoding"]),
                    content_type=row.get("content_type"), sha256=row.get("sha256"),
                    direction=direction,
                )
                (materialized if direction == "input" else materialized_outputs).append(item)
            inputs_root.chmod(0o500)
            yield RunFileScope(
                run_id, inputs_root, scratch_root, outputs_root, tuple(materialized),
                tuple(materialized_outputs),
            )
        except (OSError, FileStoreError) as exc:
            raise RunFileScopeUnavailable("failed to prepare Run file scope") from exc
        finally:
            if run_root.exists():
                if inputs_root.exists():
                    inputs_root.chmod(0o700)
                    for path in inputs_root.iterdir():
                        if path.is_file() and not path.is_symlink():
                            path.chmod(0o600)
                if outputs_root.exists():
                    for path in outputs_root.iterdir():
                        if path.is_file() and not path.is_symlink():
                            path.chmod(0o600)
                shutil.rmtree(run_root)

    @staticmethod
    def _logical_name(value: str) -> str:
        candidate = Path(value)
        if (
            not value or candidate.is_absolute() or len(candidate.parts) != 1
            or value in {".", ".."} or "\x00" in value
        ):
            raise RunFileScopeUnavailable("invalid logical input name")
        return value

    @staticmethod
    def _remove_stale_run_root(run_root: Path) -> None:
        if run_root.is_symlink() or not run_root.is_dir():
            raise RunFileScopeUnavailable("stale Run file scope is invalid")
        for path in run_root.rglob("*"):
            if path.is_symlink():
                raise RunFileScopeUnavailable("stale Run file scope contains a symbolic link")
            if path.is_file():
                path.chmod(0o600)
        for directory in sorted(
            (path for path in run_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts), reverse=True,
        ):
            directory.chmod(0o700)
        run_root.chmod(0o700)
        shutil.rmtree(run_root)
