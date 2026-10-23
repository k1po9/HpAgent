"""Trusted parent actions; independent of native/MCP/sandbox tool configuration."""

from actions.contracts import ActionResult
from run_domain.acceptance import ResultAdoptionService
from run_domain.capability_contracts import AcceptancePredicate
from workspace.resources import ResourceDenied

NAMES = frozenset({"result__adopt", "result__save", "result__evidence"})


def discover(*, model_facing=True):
    specs = (
        (
            "result__adopt",
            "Explicitly adopt an authorized exact result from another Run. Returns immutable metadata; does not save.",
            {"ref_id": {"type": "string"}},
            ["ref_id"],
        ),
        (
            "result__save",
            "Save a result to an authorized Workspace directory or update an exact destination with CAS. Requires current parent write grants. Returns node_id, operation_id and workspace_saved_predicate; submit that exact predicate to result__evidence with the matching criterion_id. Saving alone does not register completion evidence.",
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
                    "oneOf": [
                        {"properties": {"kind": {"const": "output_exists"}}, "not": {"anyOf": [{"required": [key]} for key in ("target_id", "operation_id", "target_revision", "verifier_key", "verifier_version")]}},
                        {"properties": {"kind": {"const": "check_passed"}}, "required": ["verifier_key", "verifier_version"], "not": {"anyOf": [{"required": [key]} for key in ("target_id", "operation_id", "target_revision")]}},
                        {"properties": {"kind": {"const": "workspace_saved"}}, "required": ["target_id", "operation_id"], "not": {"anyOf": [{"required": [key]} for key in ("verifier_key", "verifier_version")]}}
                    ],
                    "properties": {
                        "schema_version": {"const": 1},
                        "kind": {"enum": ["output_exists", "check_passed", "workspace_saved"]},
                        "subject_ref": {"type": "string", "format": "uuid", "description": "Exact ResultRef.ref_id, not file_id, node_id or artifact_version_id."},
                        "verifier_key": {"type": "string", "description": "Only check_passed: exact registered verifier key. Set null for output_exists/workspace_saved."},
                        "verifier_version": {"type": "string", "description": "Only check_passed: exact registered verifier version. Set null for output_exists/workspace_saved."},
                        "target_id": {"type": "string", "format": "uuid", "description": "Only workspace_saved: node_id from result__save. Omit for output_exists/check_passed."},
                        "operation_id": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Only workspace_saved: exact operation_id returned by result__save. Omit for other kinds."},
                        "target_revision": {"type": "integer", "minimum": 1, "description": "CAS version update only: exact revision returned by result__save. Omit for new-entry saves; do not assume revision 1."},
                    },
                },
            },
            ["criterion_id", "predicate"],
        ),
    )
    save_fields = specs[1][2]
    for field in ("ref_id", "directory_id", "entry_id"):
        save_fields[field]["format"] = "uuid"
    save_fields["directory_id"]["description"] = "New entry: exact authorized destination directory UUID; also provide name."
    save_fields["entry_id"]["description"] = "CAS update only: existing node UUID; also provide expected_revision and expected_sha256, omit name/directory_id."
    save_fields["name"]["minLength"] = 1
    save_fields["expected_revision"]["minimum"] = 1
    save_fields["expected_sha256"]["pattern"] = "^[a-f0-9]{64}$"
    specs = [
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
                    **({"oneOf": [
                        {"required": ["directory_id", "name"], "not": {"anyOf": [{"required": [key]} for key in ("entry_id", "expected_revision", "expected_sha256")]}},
                        {"required": ["entry_id", "expected_revision", "expected_sha256"], "not": {"anyOf": [{"required": [key]} for key in ("directory_id", "name")]}}
                    ]} if name == "result__save" else {}),
                },
            },
        }
        for name, description, fields, required in specs
    ]

    if model_facing:
        from copy import deepcopy

        specs = deepcopy(specs)
        for item in specs:
            function = item['function']
            schema = function['parameters']
            if function['name'] in {'result__save', 'result__evidence'}:
                function['description'] += ' Include every declared field; use null for fields that do not apply. The server canonicalizes absent values before enforcing the conditional contract.'
                if function['name'] == 'result__evidence':
                    function['description'] += ' output_exists uses only kind/subject_ref; check_passed adds verifier_key/version; workspace_saved adds target_id/operation_id and revision only for CAS updates. All fields from other kinds must be null.'
            if function['name'] == 'result__save':
                schema.pop('oneOf', None)
                schema['required'] = list(schema['properties'])
                for name, field in schema['properties'].items():
                    if name != 'ref_id':
                        field['type'] = [field['type'], 'null']
            elif function['name'] == 'result__evidence':
                predicate = schema['properties']['predicate']
                predicate.pop('oneOf', None)
                predicate['required'] = list(predicate['properties'])
                for name, field in predicate['properties'].items():
                    if name not in {'kind', 'subject_ref', 'schema_version'}:
                        field['type'] = [field['type'], 'null']
        return specs
    return specs


def execute(database, facts, action, operation_id):
    from dataclasses import replace

    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import ValidationError

    from run_domain.capability_contracts import normalize_inputs

    from .capabilities import input_validation_error

    spec = next((item['function'] for item in discover(model_facing=False) if item['function']['name'] == action.name), None)
    if spec is None:
        return ActionResult(action, success=False, error="unregistered parent result action", metadata={})
    schema = spec['parameters']
    try:
        arguments = normalize_inputs(action.arguments, schema)
        if arguments.get('expected_revision', False) is None:
            arguments.pop('expected_revision')
        if isinstance(arguments.get('predicate'), dict):
            arguments['predicate'] = normalize_inputs(arguments['predicate'], schema['properties']['predicate'])
            if arguments['predicate'].get('target_revision', False) is None:
                arguments['predicate'].pop('target_revision')
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(arguments)
    except ValidationError as exc:
        return ActionResult(action, success=False, error=input_validation_error(exc), metadata={})
    except (TypeError, ValueError):
        return ActionResult(action, success=False, error="parent result arguments require a finite JSON object", metadata={})
    action = replace(action, arguments=arguments)
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
