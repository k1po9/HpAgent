"""Trusted parent actions; independent of native/MCP/sandbox tool configuration."""

from actions.contracts import ActionResult
from run_domain.acceptance import ResultAdoptionService
from run_domain.capability_contracts import AcceptancePredicate
from workspace.resources import ResourceDenied

NAMES = frozenset({"result__adopt", "result__save", "result__evidence"})


def discover():
    specs = (
        (
            "result__adopt",
            "Explicitly adopt an authorized exact result from another Run. Returns immutable metadata; does not save.",
            {"ref_id": {"type": "string"}},
            ["ref_id"],
        ),
        (
            "result__save",
            "Save a result to an authorized Workspace directory or update an exact destination with CAS. Requires current parent write grants.",
            {
                k: {"type": "integer" if k == "expected_revision" else "string"}
                for k in (
                    "ref_id",
                    "directory_id",
                    "name",
                    "entry_id",
                    "expected_revision",
                    "expected_sha256",
                )
            },
            ["ref_id"],
        ),
        (
            "result__evidence",
            "Evaluate a registered predicate against an exact output, verifier version or committed save. Returns evidence for a Work criterion.",
            {
                "criterion_id": {"type": "string"},
                "predicate": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "subject_ref"],
                    "properties": {
                        "schema_version": {"const": 1},
                        "kind": {"enum": ["output_exists", "check_passed", "workspace_saved"]},
                        "subject_ref": {"type": "string", "format": "uuid"},
                        "verifier_key": {"type": "string"},
                        "verifier_version": {"type": "string"},
                        "target_id": {"type": "string", "format": "uuid"},
                        "operation_id": {"type": "string", "minLength": 1, "maxLength": 200},
                        "target_revision": {"type": "integer", "minimum": 1},
                    },
                },
            },
            ["criterion_id", "predicate"],
        ),
    )
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": fields,
                    "required": required,
                },
            },
        }
        for name, description, fields, required in specs
    ]


def execute(database, facts, action, operation_id):
    service = ResultAdoptionService(database, facts)
    try:
        if action.name == "result__adopt":
            result = service.adopt(**action.arguments)
        elif action.name == "result__save":
            result = service.save(**action.arguments, operation_id=operation_id)
        elif action.name == "result__evidence":
            result = service.evidence(
                action.arguments["criterion_id"],
                AcceptancePredicate(**action.arguments["predicate"]),
            )
        else:
            raise ValueError("unregistered parent result action")
        return ActionResult(action, success=True, output=result, summary=result, metadata={})
    except (TypeError, ValueError, PermissionError, ResourceDenied) as exc:
        return ActionResult(action, success=False, error=str(exc)[:500], metadata={})
