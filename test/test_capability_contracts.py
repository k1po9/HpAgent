from dataclasses import replace
from uuid import uuid4

import pytest

from run_domain.capability_contracts import (
    AcceptancePredicate,
    CapabilityDefinition,
    CapabilityRegistry,
    CapabilityResult,
    Check,
    ResultRef,
    canonical,
    resource_scope,
)


def definition():
    return CapabilityDefinition(
        key="test.echo",
        contract_version=1,
        implementation_version="1",
        description="Test only",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                k: {}
                for k in (
                    "schema_version",
                    "outcome",
                    "summary",
                    "outputs",
                    "checks",
                    "gaps",
                    "error",
                    "provenance",
                )
            },
            "additionalProperties": False,
        },
        executor_kind="fixed_workflow",
        execution_mode="isolated_execution",
        allowed_surfaces=("chat", "work"),
        required_services=(),
        resource_operations=("read_content",),
        side_effect_policy="candidate_output",
        limits={
            "schema_version": 1,
            "max_duration_seconds": 60,
            "max_model_calls": 0,
            "max_tool_calls": 2,
        },
        retry_policy={"schema_version": 1, "max_attempts": 2},
        cancellation_policy="cooperative_bounded",
        verifier_keys=("test.structure",),
    )


def scope(**fields):
    return {
        "schema_version": 1,
        "node_ids": [],
        "file_ids": [],
        "tool_names": [],
        "result_ids": [],
        **fields,
    }


def test_registry_snapshot_discovery_and_versions():
    value = definition()
    registry = CapabilityRegistry((value,))
    value.input_schema["properties"].clear()
    copy = registry.get("test.echo", 1)
    assert "text" in copy["input_schema"]["properties"]
    copy["limits"].clear()
    assert registry.discover("work", set())[0]["limits"]["max_duration_seconds"] == 60
    assert registry.discover("qq", set()) == []
    with pytest.raises(ValueError, match="duplicate"):
        CapabilityRegistry((definition(), definition()))
    with pytest.raises(ValueError, match="unregistered"):
        registry.get("test.echo", 2)
    required = replace(definition(), required_services=("isolated",))
    assert CapabilityRegistry((required,)).discover("work", set()) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_mode", "inline"),
        ("side_effect_policy", "external_publish"),
        ("resource_operations", ("delete_entry",)),
        ("cancellation_policy", "ignore"),
        ("contract_version", True),
        (
            "input_schema",
            {"type": "object", "additionalProperties": False, "$ref": "https://example.com"},
        ),
    ],
)
def test_definition_rejects_unsupported_authority(field, value):
    with pytest.raises(ValueError):
        replace(definition(), **{field: value}).to_dict()


def test_scope_is_explicit_normalized_and_bounded():
    a, b = str(uuid4()), str(uuid4())
    assert resource_scope(scope(file_ids=[b, a, b]))["file_ids"] == sorted([a, b])
    with pytest.raises(ValueError):
        resource_scope({"schema_version": 1})
    with pytest.raises(ValueError):
        canonical({"x": float("nan")})


def test_exact_result_subject_check_and_outcome_are_independent():
    file = str(uuid4())
    ref = ResultRef(str(uuid4()), "markdown", file, file, "caller-op", str(uuid4()), "producer-op")
    evidence = replace(ref, ref_id=str(uuid4()), type="check_evidence")
    check = Check("test.structure", "1", ref.ref_id, "failed", evidence.ref_id)
    result = CapabilityResult("insufficient", "Draft retained", (ref, evidence), (check,))
    assert result.to_dict()["checks"][0]["verdict"] == "failed"
    with pytest.raises(ValueError, match="exact subject"):
        replace(result, checks=(replace(check, subject_ref=str(uuid4())),)).to_dict()
    with pytest.raises(ValueError, match="immutable file"):
        replace(ref, version="latest").to_dict()
    with pytest.raises(ValueError, match="duplicate"):
        CapabilityResult("succeeded", "", (ref, ref)).to_dict()


@pytest.mark.parametrize(
    "kind",
    ["output_exists", "check_passed", "workspace_saved", "user_accepted", "delivery_accepted"],
)
def test_typed_predicate_contract(kind):
    fields = (
        {"verifier_key": "test.structure", "verifier_version": "1"}
        if kind == "check_passed"
        else ({"target_id": str(uuid4())} if kind not in {"output_exists", "check_passed"} else {})
    )
    if kind == "workspace_saved":
        fields["operation_id"] = "save:fixture"
        fields["target_revision"] = 2
    assert AcceptancePredicate(kind, str(uuid4()), **fields).to_dict()["schema_version"] == 1
    with pytest.raises(ValueError):
        AcceptancePredicate("eval_model_code", str(uuid4())).to_dict()


def test_observation_uses_producer_pointer_and_its_own_version():
    from run_domain.capability_contracts import CapabilityObservation

    observation = CapabilityObservation("caller", "producer", "capability-result:caller")
    assert observation.to_dict()["schema_version"] == 1
    with pytest.raises(ValueError, match="exact producer"):
        replace(observation, producer_result_ref="capability-result:another").to_dict()
    with pytest.raises(ValueError, match="schema"):
        replace(observation, schema_version=True).to_dict()


@pytest.mark.parametrize('inputs, expected', [
    ({'text': 'secret-value', 'source_ref': 'secret-value'}, 'choose exactly one'),
    ({'inputs': {'text': 'secret-value'}}, 'allowed fields'),
    ({'source_ref': 'ref', 'instruction': 'secret-value' * 1000}, 'maxLength'),
])
def test_schema_error_explains_fields_without_echoing_input(inputs, expected):
    from jsonschema import Draft202012Validator

    from agent_activities.capabilities import input_validation_error
    from web_artifacts.capability import html_definition
    with pytest.raises(Exception) as error:
        Draft202012Validator(html_definition().input_schema).validate(inputs)
    message = input_validation_error(error.value)
    assert expected in message
    assert 'secret-value' not in message


def test_optional_empty_html_fields_normalize_before_exclusive_form_validation():
    from jsonschema import Draft202012Validator

    from run_domain.capability_contracts import normalize_inputs
    from web_artifacts.capability import html_definition, html_scope
    schema = html_definition().input_schema
    ref = str(uuid4())
    value = normalize_inputs({'text': '', 'source_ref': ref, 'previous_ref': '', 'instruction': None}, schema)
    assert value == {'source_ref': ref}
    Draft202012Validator(schema).validate(value)
    assert html_scope(value)['result_ids'] == [ref]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(normalize_inputs({'text': 'meaningful content', 'source_ref': ref}, schema))
    assert normalize_inputs({'text': None}, definition().input_schema) == {'text': None}


def test_save_receipt_supplies_exact_evaluator_input_without_creating_evidence():
    from run_domain.acceptance import ResultAdoptionService
    ref, node = str(uuid4()), str(uuid4())
    receipt = ResultAdoptionService._save_receipt({'node_id': node}, ref, 'actual-save-operation')
    assert receipt['operation_id'] == 'actual-save-operation'
    assert receipt['subject_ref'] == ref
    predicate = receipt['workspace_saved_predicate']
    assert predicate == {'schema_version': 1, 'kind': 'workspace_saved', 'subject_ref': ref,
                         'target_id': node, 'operation_id': 'actual-save-operation'}
    assert AcceptancePredicate(**predicate).to_dict()['operation_id'] == receipt['operation_id']
    assert 'evidence_id' not in receipt


def test_parent_html_tool_requires_explicit_instruction_without_changing_fixed_v1_callers():
    from jsonschema import Draft202012Validator

    from agent_activities.capabilities import parent_input_schema
    from web_artifacts.capability import html_definition

    definition = html_definition().to_dict()
    source = {"source_ref": str(uuid4())}
    Draft202012Validator(definition["input_schema"]).validate(source)
    validator = Draft202012Validator(parent_input_schema(definition))
    for missing in (source, {**source, "instruction": ""}):
        with pytest.raises(Exception):
            validator.validate(missing)
    validator.validate({**source, "instruction": "Preserve source facts; include the requested accessible filter controls."})
    assert "required" not in definition["input_schema"]


@pytest.mark.parametrize("predicate", [
    {"kind": "workspace_saved", "subject_ref": str(uuid4()), "operation_id": "save", "target_revision": 1},
    {"kind": "check_passed", "subject_ref": str(uuid4())},
    {"kind": "output_exists", "subject_ref": str(uuid4()), "target_id": str(uuid4())},
])
def test_parent_result_predicate_forms_reject_missing_or_irrelevant_fields_before_io(predicate):
    from unittest.mock import patch

    from actions.contracts import ActionRequest
    from agent_activities.result_tools import execute

    action = ActionRequest(id="call", name="result__evidence", arguments={"criterion_id": "saved", "predicate": predicate})
    with patch("agent_activities.result_tools.ResultAdoptionService") as service:
        result = execute(None, None, action, "tool-operation")
    assert result.success is False and "schema" in result.error
    service.assert_not_called()


def test_parent_result_save_normalizes_absent_version_fields_before_side_effect():
    from unittest.mock import patch

    from actions.contracts import ActionRequest
    from agent_activities.result_tools import execute

    args = {"ref_id": str(uuid4()), "directory_id": str(uuid4()), "name": "page.html", "entry_id": None,
            "expected_revision": None, "expected_sha256": None}
    with patch("agent_activities.result_tools.ResultAdoptionService") as service:
        service.return_value.save.return_value = {"node_id": str(uuid4())}
        result = execute(None, None, ActionRequest("call", "result__save", args), "actual-operation")
    assert result.success
    service.return_value.save.assert_called_once_with(ref_id=args["ref_id"], directory_id=args["directory_id"], name="page.html", operation_id="actual-operation")


def test_model_wire_schemas_declare_complete_nullable_fields_and_internal_forms_stay_strict():
    from jsonschema import Draft202012Validator

    from agent_activities.capabilities import parent_input_schema
    from agent_activities.result_tools import discover
    from web_artifacts.capability import html_definition

    specs = {item["function"]["name"]: item["function"]["parameters"] for item in discover()}
    predicate = specs["result__evidence"]["properties"]["predicate"]
    assert "oneOf" not in predicate
    assert set(predicate["required"]) == set(predicate["properties"])
    value = {key: None for key in predicate["properties"]}
    value.update(schema_version=1, kind="output_exists", subject_ref=str(uuid4()))
    Draft202012Validator(specs["result__evidence"]).validate({"criterion_id": "output", "predicate": value})
    wire = parent_input_schema(html_definition().to_dict(), model_facing=True)
    Draft202012Validator(wire).validate({"text": None, "source_ref": str(uuid4()), "previous_ref": None, "instruction": "Follow the requested behavior."})
    assert "oneOf" not in wire
    internal = {item["function"]["name"]: item["function"]["parameters"] for item in discover(model_facing=False)}
    assert "oneOf" in internal["result__evidence"]["properties"]["predicate"]


def test_complete_nullable_wire_predicate_is_canonicalized_before_evaluation():
    from unittest.mock import patch

    from actions.contracts import ActionRequest
    from agent_activities.result_tools import execute

    subject = str(uuid4())
    predicate = {"schema_version": 1, "kind": "output_exists", "subject_ref": subject,
                 "verifier_key": None, "verifier_version": None, "target_id": None,
                 "operation_id": None, "target_revision": None}
    with patch("agent_activities.result_tools.ResultAdoptionService") as service:
        service.return_value.evidence.return_value = {"ref": str(uuid4())}
        result = execute(None, None, ActionRequest("call", "result__evidence", {"criterion_id": "output", "predicate": predicate}), "op")
    assert result.success
    evaluated = service.return_value.evidence.call_args.args[1]
    assert evaluated.subject_ref == subject and evaluated.target_id is None


def test_wrong_predicate_kind_reports_inapplicable_fields_without_echoing_values():
    from unittest.mock import patch

    from actions.contracts import ActionRequest
    from agent_activities.result_tools import execute

    args = {"criterion_id": "output", "predicate": {"kind": "output_exists", "subject_ref": str(uuid4()),
            "verifier_key": "private-value", "verifier_version": "private-version"}}
    with patch("agent_activities.result_tools.ResultAdoptionService") as service:
        result = execute(None, None, ActionRequest("call", "result__evidence", args), "op")
    assert not result.success and "null: verifier_key, verifier_version" in result.error
    assert "private-value" not in result.error and "private-version" not in result.error
    service.assert_not_called()
