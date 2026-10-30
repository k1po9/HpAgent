"""Transactional capability facts. Callers supply the transaction; no Workflow or external I/O.

These services require an explicit live Execution fence, including for reads.
They are internal composition contracts, not model tools or HTTP endpoints.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from persistence.uow import UnitOfWork
from run_domain.capability_contracts import (
    CapabilityRegistry,
    CapabilityResult,
    ResultProvenance,
    ResultRef,
    canonical,
    limits,
    resource_scope,
)
from run_domain.execution import StaleFencingToken, assert_execution_fence, lock_execution
from run_domain.execution_context import execution_fence
from run_domain.results import complete_operation, result_digest
from workspace.resources import ResourceDenied, ResourcePolicy

_NAMESPACE = UUID("7476905f-e994-5c64-a2ce-504762e9ca1d")


class CapabilityFacts:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        services: frozenset[str] = frozenset(),
        atomic_tools: frozenset[str] = frozenset(),
    ):
        self.registry = registry
        self.services = services
        self.atomic_tools = atomic_tools

    @staticmethod
    def _context(uow: UnitOfWork) -> tuple[Any, Any]:
        fence = execution_fence.get()
        if fence is None:
            raise StaleFencingToken("capability facts require an explicit Execution fence")
        assert_execution_fence(uow)
        execution = lock_execution(uow, fence[0], fence[1], fence[2])
        run = uow.execute(
            "SELECT * FROM runs WHERE account_id=%s AND run_id=%s", (UUID(fence[0]), UUID(fence[1]))
        ).fetchone()
        return run, execution

    def create(
        self,
        uow: UnitOfWork,
        *,
        operation_id: str,
        capability_key: str,
        contract_version: int,
        inputs: dict[str, Any],
        scope: dict[str, Any],
        invocation_limits: dict[str, Any],
    ) -> dict[str, Any]:
        """Deduplicate only the same caller Operation and complete canonical descriptor."""
        run, parent = self._context(uow)
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 300:
            raise ValueError("invalid invocation Operation identity")
        if parent["role"] != "root":
            raise ResourceDenied("only root can invoke an isolated capability")
        definition = self.registry.get(capability_key, contract_version)
        if definition["execution_mode"] != "isolated_execution":
            raise ValueError("atomic tools do not create invocation facts")
        if (
            run["source_kind"] not in definition["allowed_surfaces"]
            or not set(definition["required_services"]) <= self.services
        ):
            raise ResourceDenied("capability is unavailable on this surface or service set")
        from run_domain.capability_contracts import normalize_inputs

        inputs = normalize_inputs(inputs, definition["input_schema"])
        Draft202012Validator(definition["input_schema"]).validate(inputs)
        scope = resource_scope(scope)
        invocation_limits = limits(invocation_limits)
        if any(
            invocation_limits[k] > definition["limits"][k]
            for k in invocation_limits
            if k != "schema_version"
        ):
            raise ResourceDenied("invocation limits exceed definition")
        if not set(scope["tool_names"]) <= self.atomic_tools:
            raise ResourceDenied("tool is outside trusted capability tool set")
        # Operation lock also serializes concurrent registration and uncertain retries.
        operation = uow.execute(
            "SELECT * FROM execution_operations WHERE account_id=%s AND run_id=%s "
            "AND execution_id=%s AND operation_id=%s FOR UPDATE",
            (run["account_id"], run["run_id"], parent["execution_id"], operation_id),
        ).fetchone()
        if operation is None or operation["operation_type"] != "tool":
            raise ResourceDenied("invocation must belong to the caller tool Operation")
        descriptor = canonical(
            {
                "schema_version": 1,
                "definition": definition,
                "inputs": inputs,
                "resource_scope": scope,
                "limits": invocation_limits,
            },
            limit=65536 if "execution_policy" in definition else 32768,
        )
        if "execution_policy" in definition and len(json.dumps(
            descriptor, ensure_ascii=False, separators=(", ", ": "), allow_nan=False,
        ).encode()) > 65536:
            raise ValueError("capability.descriptor: exceeds 65536 byte storage limit")
        prior = uow.execute(
            "SELECT * FROM capability_invocations WHERE invocation_id=%s", (operation_id,)
        ).fetchone()
        if prior:
            if prior["descriptor"] != descriptor:
                raise ValueError("invocation identity input conflict")
            return dict(prior)
        if run['source_kind'] == 'work':
            from run_domain.lifecycle import RunLifecycleService
            from work_domain.progress import ProgressBlocked, blocked_reason
            work = RunLifecycleService.check_work(uow, run)
            reason = blocked_reason(uow, work)
            if reason:
                raise ProgressBlocked(reason)
        policy = ResourcePolicy(uow)
        for node in scope["node_ids"]:
            candidate = uow.execute(
                "SELECT s.subject_kind,s.subject_id FROM run_resource_candidates c "
                "JOIN run_resource_snapshots s USING(account_id,run_id) "
                "WHERE c.account_id=%s AND c.run_id=%s AND c.node_id=%s AND s.status='ready'",
                (run["account_id"], run["run_id"], UUID(node)),
            ).fetchone()
            if candidate is None or any(
                not policy._grants(
                    uow,
                    run["account_id"],
                    candidate["subject_kind"],
                    candidate["subject_id"],
                    UUID(node),
                    action,
                )
                for action in definition["resource_operations"]
            ):
                raise ResourceDenied("node is not a currently authorized frozen candidate")
        for file_id in scope["file_ids"]:
            if not policy._file_authorized_in_uow(
                uow, run["account_id"], run["run_id"], UUID(file_id)
            ):
                raise ResourceDenied("file is outside current parent authorization")
        for ref_id in scope["result_ids"]:
            self.read_ref(uow, UUID(ref_id))
        child = uuid5(_NAMESPACE, operation_id)
        row = uow.execute(
            "INSERT INTO capability_invocations(invocation_id,account_id,run_id,caller_execution_id,"
            "child_execution_id,capability_key,contract_version,implementation_version,descriptor,"
            "descriptor_digest,requirement_revision,work_control_epoch) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                operation_id,
                run["account_id"],
                run["run_id"],
                parent["execution_id"],
                child,
                capability_key,
                contract_version,
                definition["implementation_version"],
                Jsonb(descriptor),
                result_digest(descriptor),
                run["requirement_revision"],
                run["work_control_epoch"],
            ),
        ).fetchone()
        uow.execute(
            "INSERT INTO run_executions(execution_id,account_id,run_id,role,parent_execution_id,"
            "branch_key,invocation_id,context_manifest,resource_scope) "
            "VALUES (%s,%s,%s,'capability',%s,'capability',%s,%s,%s)",
            (
                child,
                run["account_id"],
                run["run_id"],
                parent["execution_id"],
                operation_id,
                Jsonb({"schema_version": 1, "invocation_id": operation_id}),
                Jsonb(scope),
            ),
        )
        for ref_id in scope["result_ids"]:
            uow.execute(
                "INSERT INTO capability_input_refs(account_id,invocation_id,ref_id) VALUES (%s,%s,%s)",
                (run["account_id"], operation_id, UUID(ref_id)),
            )
        return dict(row)

    def publish_result(
        self,
        uow: UnitOfWork,
        *,
        invocation_id: str,
        producing_operation_id: str,
        result: CapabilityResult,
    ) -> None:
        """Commit producer receipt, typed refs and checks together. Never completes caller/Run."""
        run, child = self._context(uow)
        if child["role"] != "capability" or child["invocation_id"] != invocation_id:
            raise ResourceDenied("result producer is outside invocation")
        invocation = uow.execute(
            "SELECT * FROM capability_invocations WHERE account_id=%s AND invocation_id=%s",
            (run["account_id"], invocation_id),
        ).fetchone()
        payload = result.to_dict()
        provenance = ResultProvenance(
            invocation_id, str(child["execution_id"]), producing_operation_id
        ).to_dict()
        if payload["provenance"] is not None and payload["provenance"] != provenance:
            raise ResourceDenied("result provenance does not match current producer")
        payload["provenance"] = provenance
        definition = invocation["descriptor"]["definition"]
        Draft202012Validator(definition["output_schema"]).validate(payload)
        for check in payload["checks"]:
            if check["verifier_key"] not in definition["verifier_keys"]:
                raise ResourceDenied("unregistered verifier for capability")
        for ref in payload["outputs"]:
            if (
                ref["producing_invocation_id"] != invocation_id
                or ref["producing_execution_id"] != str(child["execution_id"])
                or ref["producing_operation_id"] != producing_operation_id
            ):
                raise ResourceDenied("output producer mismatch")
            # A candidate must actually belong to the producing Run; inputs cannot be relabelled.
            if ref["type"] != "artifact_version":
                file = uow.execute(
                    "SELECT source_run_id,purpose FROM stored_files "
                    "WHERE account_id=%s AND file_id=%s",
                    (run["account_id"], UUID(ref["object_id"])),
                ).fetchone()
                if (
                    file is None
                    or file["source_run_id"] != run["run_id"]
                    or file["purpose"] != "output"
                ):
                    raise ResourceDenied("candidate file must be an output of producing Run")
            else:
                artifact = uow.execute(
                    "SELECT 1 FROM artifact_versions WHERE account_id=%s "
                    "AND artifact_id=%s AND artifact_version_id=%s AND producing_run_id=%s "
                    "AND producing_execution_id=%s",
                    (
                        run["account_id"],
                        UUID(ref["object_id"]),
                        UUID(ref["version"]),
                        run["run_id"],
                        child["execution_id"],
                    ),
                ).fetchone()
                if artifact is None:
                    raise ResourceDenied("candidate artifact must belong to producing Run")
        prior = uow.execute(
            "SELECT * FROM capability_results WHERE account_id=%s AND invocation_id=%s",
            (run["account_id"], invocation_id),
        ).fetchone()
        if prior:
            if (
                prior["producing_operation_id"] != producing_operation_id
                or prior["payload"] != payload
            ):
                raise ValueError("invocation result conflict")
            return
        complete_operation(
            uow, producing_operation_id, f"capability-result:{invocation_id}", payload
        )
        uow.execute(
            "INSERT INTO capability_results(invocation_id,account_id,run_id,producing_execution_id,"
            "producing_operation_id,payload) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                invocation_id,
                run["account_id"],
                run["run_id"],
                child["execution_id"],
                producing_operation_id,
                Jsonb(payload),
            ),
        )
        for ref in payload["outputs"]:
            artifact = ref["type"] == "artifact_version"
            uow.execute(
                "INSERT INTO capability_result_refs(ref_id,account_id,invocation_id,type,"
                "file_id,artifact_id,artifact_version_id,sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    UUID(ref["ref_id"]),
                    run["account_id"],
                    invocation_id,
                    ref["type"],
                    None if artifact else UUID(ref["object_id"]),
                    UUID(ref["object_id"]) if artifact else None,
                    UUID(ref["version"]) if artifact else None,
                    ref["sha256"],
                ),
            )
        for check in payload["checks"]:
            uow.execute(
                "INSERT INTO capability_result_checks(account_id,invocation_id,verifier_key,"
                "verifier_version,subject_ref,evidence_ref,verdict) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    run["account_id"],
                    invocation_id,
                    check["verifier_key"],
                    check["verifier_version"],
                    UUID(check["subject_ref"]),
                    UUID(check["evidence_ref"]),
                    check["verdict"],
                ),
            )
        uow.execute(
            "UPDATE run_executions SET result_ref=%s,updated_at=now() WHERE execution_id=%s",
            (f"capability-result:{invocation_id}", child["execution_id"]),
        )

        from work_domain.progress import record_result
        record_result(uow, run, invocation, payload)

    def read_ref(self, uow: UnitOfWork, ref_id: UUID) -> ResultRef:
        """Resolve exact metadata under current authority. This is not a bearer reference."""
        run, execution = self._context(uow)
        row = uow.execute(
            "SELECT f.*,r.run_id AS producing_run_id,r.producing_execution_id,r.producing_operation_id,"
            "i.caller_execution_id FROM capability_result_refs f JOIN capability_results r "
            "USING(account_id,invocation_id) JOIN capability_invocations i USING(account_id,invocation_id) "
            "WHERE f.account_id=%s AND f.ref_id=%s",
            (run["account_id"], ref_id),
        ).fetchone()
        if row is None:
            raise ResourceDenied("result reference is unavailable")
        local = row["producing_run_id"] == run["run_id"] and execution["execution_id"] in {
            row["caller_execution_id"],
            row["producing_execution_id"],
        }
        inherited = (
            execution["role"] == "capability"
            and uow.execute(
                "SELECT 1 FROM capability_input_refs WHERE account_id=%s AND invocation_id=%s AND ref_id=%s",
                (run["account_id"], execution["invocation_id"], ref_id),
            ).fetchone()
            is not None
        )
        # Explicit same-Run handoff is a result edge, not an expansion of frozen external files.
        local = local or (inherited and row["producing_run_id"] == run["run_id"])
        if not local and not inherited:
            if execution["role"] != "root":
                raise ResourceDenied("result was not explicitly passed to this child")
            # Cross-Run reuse requires explicit adoption, plus live resource checks on every read.
            if not uow.execute(
                "SELECT 1 FROM capability_result_adoptions WHERE account_id=%s "
                "AND execution_id=%s AND ref_id=%s",
                (run["account_id"], execution["execution_id"], ref_id),
            ).fetchone():
                raise ResourceDenied("result must be explicitly adopted by this Execution")
        if not local:
            self._authorize_resource(uow, run, row)
        elif row["file_id"] is not None:
            if not uow.execute(
                "SELECT 1 FROM stored_files WHERE account_id=%s AND file_id=%s AND status='ready'",
                (run["account_id"], row["file_id"]),
            ).fetchone():
                raise ResourceDenied("result file is unavailable")
        return ResultRef(
            ref_id=str(row["ref_id"]),
            type=row["type"],
            object_id=str(row["file_id"] or row["artifact_id"]),
            version=str(row["file_id"] or row["artifact_version_id"]),
            producing_invocation_id=row["invocation_id"],
            producing_execution_id=str(row["producing_execution_id"]),
            producing_operation_id=row["producing_operation_id"],
            sha256=row["sha256"],
        )

    @staticmethod
    def _authorize_resource(uow: UnitOfWork, run: Any, ref: Any) -> None:
        file_id = ref["file_id"]
        if file_id is None:
            artifact = uow.execute(
                "SELECT file_id FROM artifact_versions WHERE account_id=%s "
                "AND artifact_version_id=%s AND status='completed'",
                (run["account_id"], ref["artifact_version_id"]),
            ).fetchone()
            file_id = artifact["file_id"] if artifact else None
        if file_id is None or not ResourcePolicy(uow)._subject_file_authorized_in_uow(
            uow, run["account_id"], run["run_id"], file_id
        ):
            raise ResourceDenied("current resource authorization is absent")
        if not uow.execute(
            "SELECT 1 FROM stored_files WHERE account_id=%s AND file_id=%s AND status='ready'",
            (run["account_id"], file_id),
        ).fetchone():
            raise ResourceDenied("result file is unavailable")

    @staticmethod
    def authorize_result_file(uow, account_id, file_id):
        facts = CapabilityFacts(CapabilityRegistry(()))
        refs = uow.execute("SELECT ref_id FROM capability_result_refs WHERE account_id=%s AND (file_id=%s OR artifact_version_id IN (SELECT artifact_version_id FROM artifact_versions WHERE account_id=%s AND file_id=%s))", (account_id, file_id, account_id, file_id)).fetchall()
        for ref in refs:
            try:
                facts.read_ref(uow, ref["ref_id"])
                return
            except ResourceDenied:
                continue
        raise ResourceDenied("file is not an authorized result")

    def adopt(self, uow: UnitOfWork, ref_id: UUID) -> dict[str, Any]:
        """Fix a prior result to the current root revision; no producer rewrite or save."""
        run, root = self._context(uow)
        if root["role"] != "root":
            raise ResourceDenied("only root can adopt results")
        ref = uow.execute(
            "SELECT * FROM capability_result_refs WHERE account_id=%s AND ref_id=%s",
            (run["account_id"], ref_id),
        ).fetchone()
        if ref is None:
            raise ResourceDenied("result reference is unavailable")
        self._authorize_resource(uow, run, ref)
        return dict(
            uow.execute(
                "INSERT INTO capability_result_adoptions(account_id,run_id,execution_id,ref_id,"
                "requirement_revision,work_control_epoch) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING *",
                (
                    run["account_id"],
                    run["run_id"],
                    root["execution_id"],
                    ref_id,
                    run["requirement_revision"],
                    run["work_control_epoch"],
                ),
            ).fetchone()
            or uow.execute(
                "SELECT * FROM capability_result_adoptions WHERE account_id=%s "
                "AND execution_id=%s AND ref_id=%s",
                (run["account_id"], root["execution_id"], ref_id),
            ).fetchone()
        )

    def read_result(self, uow: UnitOfWork, invocation_id: str) -> dict[str, Any]:
        run, execution = self._context(uow)
        row = uow.execute(
            "SELECT r.*,i.caller_execution_id FROM capability_results r "
            "JOIN capability_invocations i USING(account_id,invocation_id) "
            "WHERE r.account_id=%s AND r.invocation_id=%s AND r.run_id=%s",
            (run["account_id"], invocation_id, run["run_id"]),
        ).fetchone()
        if row is None or execution["execution_id"] not in {
            row["caller_execution_id"],
            row["producing_execution_id"],
        }:
            raise ResourceDenied("invocation result is outside caller/producer context")
        for ref in row["payload"]["outputs"]:
            self.read_ref(uow, UUID(ref["ref_id"]))
        return dict(row["payload"])

    def record_observation(
        self,
        uow: UnitOfWork,
        *,
        invocation_id: str,
        producing_operation_id: str,
        transcript_id: str,
        sequence: int,
    ) -> None:
        """Use the same UoW as caller completion and tool_result transcript append."""
        run, parent = self._context(uow)
        self.read_result(uow, invocation_id)
        if parent["role"] != "root":
            raise ResourceDenied("only caller root can observe capability result")
        values = (
            invocation_id,
            run["account_id"],
            run["run_id"],
            parent["execution_id"],
            producing_operation_id,
            transcript_id,
            sequence,
        )
        prior = uow.execute(
            "SELECT * FROM capability_observations WHERE invocation_id=%s", (invocation_id,)
        ).fetchone()
        if prior:
            if (
                prior["account_id"],
                prior["run_id"],
                prior["caller_execution_id"],
                prior["producing_operation_id"],
                prior["transcript_id"],
                prior["sequence"],
            ) != values[1:]:
                raise ValueError("parent observation identity conflict")
            return
        uow.execute(
            "INSERT INTO capability_observations(invocation_id,account_id,run_id,caller_execution_id,"
            "producing_operation_id,transcript_id,sequence) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            values,
        )
