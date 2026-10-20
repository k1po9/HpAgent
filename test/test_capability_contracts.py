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
