"""Version 1 capability values. No runtime, I/O or model-controlled authority."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from work_domain.models import bounded


def canonical(value: dict[str, Any], limit: int = 32768) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("capability value must be an object")
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if len(raw.encode()) > limit:
        raise ValueError("capability value exceeds size limit")
    return dict(json.loads(raw))


def key(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", value):
        raise ValueError("invalid capability key")


@dataclass(frozen=True)
class CapabilityDefinition:
    key: str
    contract_version: int
    implementation_version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    executor_kind: str
    execution_mode: str
    allowed_surfaces: tuple[str, ...]
    required_services: tuple[str, ...]
    resource_operations: tuple[str, ...]
    side_effect_policy: str
    limits: dict[str, Any]
    retry_policy: dict[str, Any]
    cancellation_policy: str
    verifier_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        # Definition validation runs at server registration, never in Workflow imports.
        from jsonschema import Draft202012Validator

        key(self.key)
        if type(self.contract_version) is not int or self.contract_version < 1:
            raise ValueError("invalid contract version")
        if not self.implementation_version or len(self.implementation_version) > 80:
            raise ValueError("invalid implementation version")
        if not self.description or len(self.description) > 2000:
            raise ValueError("invalid capability description")
        if self.executor_kind not in {"atomic", "fixed_workflow", "specialist_agent"}:
            raise ValueError("invalid executor kind")
        expected = "inline" if self.executor_kind == "atomic" else "isolated_execution"
        if self.execution_mode != expected:
            raise ValueError("executor/mode mismatch")
        if not self.allowed_surfaces or set(self.allowed_surfaces) - {"chat", "work"}:
            raise ValueError("invalid capability surfaces")
        if self.side_effect_policy not in {"read_only", "candidate_output"}:
            raise ValueError("capabilities cannot commit long-term or external effects")
        if set(self.resource_operations) - {"list_metadata", "read_content"}:
            raise ValueError("unsupported capability resource operation")
        if self.cancellation_policy != "cooperative_bounded":
            raise ValueError("unsupported cancellation policy")
        for schema in (self.input_schema, self.output_schema):
            Draft202012Validator.check_schema(schema)

            def reject_refs(value: Any) -> None:
                if isinstance(value, dict):
                    if "$ref" in value or "$dynamicRef" in value:
                        raise ValueError("capability schemas must be self-contained")
                    for item in value.values():
                        reject_refs(item)
                elif isinstance(value, list):
                    for item in value:
                        reject_refs(item)

            reject_refs(schema)
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                raise ValueError("capability schemas must be closed objects")
        for value in (*self.required_services, *self.verifier_keys):
            key(value)
        limits(self.limits)
        bounded(self.retry_policy, {"schema_version", "max_attempts"})
        if (
            type(self.retry_policy.get("max_attempts")) is not int
            or not 1 <= self.retry_policy["max_attempts"] <= 10
        ):
            raise ValueError("invalid retry policy")
        return canonical(asdict(self))


class CapabilityRegistry:
    """Static, explicitly injected definitions; discovery never grants execution rights."""

    def __init__(self, definitions: tuple[CapabilityDefinition, ...] = ()):
        self._definitions: dict[tuple[str, int], str] = {}
        for definition in definitions:
            value = definition.to_dict()
            identity = (definition.key, definition.contract_version)
            if identity in self._definitions:
                raise ValueError("duplicate capability definition")
            self._definitions[identity] = json.dumps(value, allow_nan=False)

    def get(self, capability_key: str, contract_version: int) -> dict[str, Any]:
        if type(contract_version) is not int or contract_version < 1:
            raise ValueError("invalid contract version")
        try:
            return dict(json.loads(self._definitions[(capability_key, contract_version)]))
        except KeyError as exc:
            raise ValueError("unregistered capability contract") from exc

    def discover(self, surface: str, services: set[str]) -> list[dict[str, Any]]:
        return [
            value
            for raw in self._definitions.values()
            if surface in (value := json.loads(raw))["allowed_surfaces"]
            and set(value["required_services"]) <= services
        ]


def limits(value: dict[str, Any]) -> dict[str, Any]:
    if type(value.get("schema_version")) is not int:
        raise ValueError("invalid schema version")
    bounded(value, {"schema_version", "max_duration_seconds", "max_model_calls", "max_tool_calls"})
    if set(value) != {
        "schema_version",
        "max_duration_seconds",
        "max_model_calls",
        "max_tool_calls",
    }:
        raise ValueError("all invocation limits are required")
    if any(
        type(value[k]) is not int or not 0 <= value[k] <= 86400
        for k in value
        if k != "schema_version"
    ):
        raise ValueError("invalid invocation limit")
    if not value["max_duration_seconds"]:
        raise ValueError("duration must be positive")
    return canonical(value)


def resource_scope(value: dict[str, Any]) -> dict[str, Any]:
    if type(value.get("schema_version")) is not int:
        raise ValueError("invalid schema version")
    bounded(value, {"schema_version", "node_ids", "file_ids", "tool_names", "result_ids"})
    if set(value) != {"schema_version", "node_ids", "file_ids", "tool_names", "result_ids"}:
        raise ValueError("explicit invocation scope is required")
    for field in ("node_ids", "file_ids", "result_ids"):
        if not isinstance(value[field], list) or len(value[field]) > 100:
            raise ValueError("invalid resource scope")
        value = {**value, field: sorted({str(UUID(item)) for item in value[field]})}
    if not isinstance(value["tool_names"], list) or len(value["tool_names"]) > 100:
        raise ValueError("invalid tool scope")
    for name in value["tool_names"]:
        key(name)
    return canonical({**value, "tool_names": sorted(set(value["tool_names"]))})


@dataclass(frozen=True)
class ResultRef:
    ref_id: str
    type: str
    object_id: str
    version: str
    producing_invocation_id: str
    producing_execution_id: str
    producing_operation_id: str
    sha256: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.type not in {"markdown", "file", "check_evidence", "artifact_version"}
        ):
            raise ValueError("unsupported result reference")
        for value in (self.ref_id, self.object_id, self.producing_execution_id):
            UUID(value)
        # File IDs are immutable byte versions; Artifact IDs require an exact version UUID.
        if self.type == "artifact_version":
            UUID(self.version)
        elif self.version != self.object_id:
            raise ValueError("file reference version must be its immutable file ID")
        if any(
            not isinstance(v, str) or not 1 <= len(v) <= 300
            for v in (self.producing_invocation_id, self.producing_operation_id)
        ):
            raise ValueError("invalid result producer")
        if self.sha256 is not None and not re.fullmatch("[a-f0-9]{64}", self.sha256):
            raise ValueError("invalid result digest")
        return canonical(asdict(self))


@dataclass(frozen=True)
class Check:
    verifier_key: str
    verifier_version: str
    subject_ref: str
    verdict: str
    evidence_ref: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        key(self.verifier_key)
        UUID(self.subject_ref)
        UUID(self.evidence_ref)
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.verdict not in {"passed", "failed", "not_checked"}
        ):
            raise ValueError("invalid check verdict")
        if not self.verifier_version or len(self.verifier_version) > 80:
            raise ValueError("invalid verifier version")
        return canonical(asdict(self))


@dataclass(frozen=True)
class ResultProvenance:
    invocation_id: str
    execution_id: str
    operation_id: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        UUID(self.execution_id)
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or any(
                not isinstance(v, str) or not 1 <= len(v) <= 300
                for v in (self.invocation_id, self.operation_id)
            )
        ):
            raise ValueError("invalid result provenance")
        return canonical(asdict(self))


@dataclass(frozen=True)
class CapabilityResult:
    outcome: str
    summary: str
    outputs: tuple[ResultRef, ...] = ()
    checks: tuple[Check, ...] = ()
    gaps: tuple[dict[str, str], ...] = ()
    error: dict[str, str] | None = None
    provenance: ResultProvenance | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.outcome not in {"succeeded", "insufficient", "failed", "cancelled", "uncertain"}
        ):
            raise ValueError("invalid capability outcome")
        if not isinstance(self.summary, str) or len(self.summary) > 2000:
            raise ValueError("invalid result summary")
        outputs = [item.to_dict() for item in self.outputs]
        refs = {item["ref_id"]: item for item in outputs}
        if len(refs) != len(outputs) or len(outputs) > 100 or len(self.checks) > 100:
            raise ValueError("duplicate or excessive output references")
        checks = [item.to_dict() for item in self.checks]
        for check in checks:
            if (
                check["subject_ref"] not in refs
                or refs.get(check["evidence_ref"], {}).get("type") != "check_evidence"
            ):
                raise ValueError("check must identify exact subject and evidence outputs")
        if len(self.gaps) > 50 or any(
            set(item) != {"code", "message"}
            or not all(isinstance(v, str) and 0 < len(v) <= 1000 for v in item.values())
            for item in self.gaps
        ):
            raise ValueError("invalid result gaps")
        if self.error is not None and (
            set(self.error) != {"code", "safe_message", "retry_class"}
            or self.error["retry_class"]
            not in {"never", "transient", "requires_input", "reconcile"}
            or not all(isinstance(v, str) and 0 < len(v) <= 1000 for v in self.error.values())
        ):
            raise ValueError("invalid safe error")
        return canonical(
            {
                "schema_version": 1,
                "outcome": self.outcome,
                "summary": self.summary,
                "outputs": outputs,
                "checks": checks,
                "gaps": self.gaps,
                "error": self.error,
                "provenance": self.provenance.to_dict() if self.provenance is not None else None,
            }
        )


@dataclass(frozen=True)
class AcceptancePredicate:
    """Typed evaluator input, independent of legacy evidence_types criteria."""

    kind: str
    subject_ref: str
    verifier_key: str | None = None
    verifier_version: str | None = None
    target_id: str | None = None
    operation_id: str | None = None
    target_revision: int | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.kind
            not in {
                "output_exists",
                "check_passed",
                "workspace_saved",
                "user_accepted",
                "delivery_accepted",
            }
        ):
            raise ValueError("unregistered acceptance predicate")
        UUID(self.subject_ref)
        if self.kind == "check_passed":
            key(self.verifier_key or "")
            if not self.verifier_version or len(self.verifier_version) > 80:
                raise ValueError("check predicate needs exact verifier version")
        elif self.verifier_key is not None or self.verifier_version is not None:
            raise ValueError("unexpected verifier")
        if self.kind in {"workspace_saved", "user_accepted", "delivery_accepted"}:
            UUID(self.target_id or "")
        elif self.target_id is not None:
            raise ValueError("unexpected predicate target")
        if self.kind == "workspace_saved":
            if not isinstance(self.operation_id, str) or not 1 <= len(self.operation_id) <= 200:
                raise ValueError("save predicate needs exact save/version operation")
            if self.target_revision is not None and (
                type(self.target_revision) is not int or self.target_revision < 1
            ):
                raise ValueError("invalid saved destination revision")
        elif self.operation_id is not None or self.target_revision is not None:
            raise ValueError("unexpected save/version identity")
        return canonical(asdict(self))


@dataclass(frozen=True)
class CapabilityObservation:
    """Pointer in both caller result_payload and tool_result event under capability_observation."""

    invocation_id: str
    producing_operation_id: str
    producer_result_ref: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported observation schema")
        if any(
            not isinstance(v, str) or not 1 <= len(v) <= 300
            for v in (self.invocation_id, self.producing_operation_id)
        ):
            raise ValueError("invalid observation identity")
        if self.producer_result_ref != f"capability-result:{self.invocation_id}":
            raise ValueError("observation must identify exact producer result")
        return canonical(asdict(self))
