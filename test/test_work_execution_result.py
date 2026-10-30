"""Work result interpretation keeps recommendations separate from current facts."""

import json
from uuid import uuid4

import pytest

from work_domain.execution_result import generic_result


@pytest.fixture
def result_facts(monkeypatch):
    from run_domain import acceptance, delegation
    from work_domain import output_evidence

    uow = object()
    run = {
        "account_id": uuid4(),
        "run_id": uuid4(),
        "requirement_revision": 3,
        "input_snapshot": {"requirement": {
            "acceptance_criteria": [
                {"id": "output", "required": True, "evidence_types": ["output_exists"]},
            ],
            "deliverable_policy": {"required": False},
        }},
    }
    work = {"checkpoint": {"checkpoint_version": 4}}
    facts = {
        "observed": [{"criterion_id": "output", "type": "output_exists", "ref": "observed"}],
        "valid_refs": {"observed", "explicit"},
        "gaps": [],
    }

    def validate(transaction, current_run, item):
        assert transaction is uow and current_run is run
        if item["ref"] not in facts["valid_refs"]:
            raise ValueError("typed evidence does not resolve to exact current facts")

    def observed(transaction, current_run):
        assert transaction is uow and current_run is run
        return facts["observed"]

    def gaps(transaction, account_id, run_id):
        assert transaction is uow
        assert (account_id, run_id) == (run["account_id"], run["run_id"])
        return facts["gaps"]

    monkeypatch.setattr(acceptance, "validate_typed_evidence", validate)
    monkeypatch.setattr(output_evidence, "observed_text_evidence", observed)
    monkeypatch.setattr(delegation, "required_branch_gaps", gaps)
    return uow, run, work, facts


def test_observed_output_can_support_narrative_but_checkpoint_keeps_provenance(result_facts):
    uow, run, work, _ = result_facts
    result, suggested = generic_result(uow, run, work, "Finished", "candidate-ref")

    assert suggested and result["kind"] == "deliverable_ready"
    assert result["evidence"] == [
        {"criterion_id": "output", "type": "output_exists", "ref": "observed"},
    ]
    assert result["checkpoint"] == {
        "schema_version": 1, "checkpoint_version": 5, "requirement_revision": 3,
        "source_run_id": str(run["run_id"]), "facts": [], "decisions": [],
        "unresolved": [], "next_steps": [], "evidence_refs": ["candidate-ref"],
    }


@pytest.mark.parametrize("protection", ["no_observation", "required_save", "other_criterion"])
def test_narrative_still_requires_all_persisted_completion_conditions(result_facts, protection):
    uow, run, work, facts = result_facts
    requirement = run["input_snapshot"]["requirement"]
    if protection == "no_observation":
        facts["observed"] = []
    elif protection == "required_save":
        requirement["deliverable_policy"]["required"] = True
    else:
        requirement["acceptance_criteria"].append(
            {"id": "check", "required": True, "evidence_types": ["check_passed"]},
        )

    result, suggested = generic_result(uow, run, work, "Finished", "candidate-ref")

    assert not suggested and result["kind"] == "progress_saved"
    assert result["continuation"]["reason"] == "result_requires_acceptance"


@pytest.mark.parametrize("kind, next_step", [
    ("progress_saved", {"schema_version": 1, "kind": "ready", "reason": "continue"}),
    ("waiting_input", {"schema_version": 1, "kind": "awaiting_input", "reason": "ask"}),
    ("waiting_due", {"schema_version": 1, "kind": "at_time", "reason": "later",
                     "due_at": "2026-10-31T10:00:00+08:00"}),
])
def test_explicit_progress_and_waits_survive_automatic_evidence(result_facts, kind, next_step):
    uow, run, work, _ = result_facts
    content = json.dumps({"schema_version": 1, "kind": kind, "continuation": next_step})

    result, suggested = generic_result(uow, run, work, content, "candidate-ref")

    assert not suggested and result["kind"] == kind
    assert result["continuation"] == next_step
    assert result["evidence"][0]["ref"] == "observed"


def test_explicit_evidence_wins_over_automatic_evidence_for_the_same_criterion(result_facts):
    uow, run, work, _ = result_facts
    content = json.dumps({"schema_version": 1, "kind": "deliverable_ready", "evidence": [
        {"criterion_id": "output", "type": "output_exists", "ref": "explicit"},
    ]})

    result, suggested = generic_result(uow, run, work, content, "candidate-ref")

    assert suggested
    assert result["evidence"] == [
        {"criterion_id": "output", "type": "output_exists", "ref": "explicit"},
    ]


def test_missing_required_branch_blocks_an_otherwise_ready_result(result_facts):
    uow, run, work, facts = result_facts
    facts["gaps"] = ["research", "review"]

    result, suggested = generic_result(uow, run, work, "Finished", "candidate-ref")

    assert not suggested and result["kind"] == "progress_saved"
    assert result["continuation"] == {
        "schema_version": 1, "kind": "blocked", "reason": "required_branch_missing:research,review",
    }


def test_required_user_acceptance_cannot_be_replaced_by_a_ready_recommendation(result_facts):
    uow, run, work, _ = result_facts
    run["input_snapshot"]["requirement"]["acceptance_criteria"].append(
        {"id": "accept", "required": True, "evidence_types": ["user_acceptance"]},
    )
    content = json.dumps({"schema_version": 1, "kind": "deliverable_ready"})

    result, suggested = generic_result(uow, run, work, content, "candidate-ref")

    assert not suggested
    assert result["continuation"]["reason"] == "user_acceptance_required"


@pytest.mark.parametrize("source", ["explicit", "observed"])
def test_current_fact_validation_is_required_for_both_evidence_sources(result_facts, source):
    uow, run, work, facts = result_facts
    facts["valid_refs"] = set()
    content = "Finished" if source == "observed" else json.dumps({
        "schema_version": 1, "kind": "deliverable_ready", "evidence": [
            {"criterion_id": "output", "type": "output_exists", "ref": "explicit"},
        ],
    })

    with pytest.raises(ValueError, match="exact current facts"):
        generic_result(uow, run, work, content, "candidate-ref")


@pytest.mark.parametrize("content", [None, "Finished", "[]", '{"schema_version": 2,"kind":"deliverable_ready"}'])
def test_parse_without_transaction_keeps_narrative_as_an_unaccepted_candidate(content):
    from work_domain.execution_result import parse_generic_result

    result, narrative = parse_generic_result(content)

    assert narrative
    assert result == {
        "schema_version": 1, "kind": "progress_saved", "evidence": [],
        "continuation": {
            "schema_version": 1, "kind": "awaiting_input", "reason": "result_requires_acceptance",
        },
    }


def test_parse_without_transaction_preserves_explicit_fields():
    from work_domain.execution_result import parse_generic_result

    candidate = {
        "schema_version": 1, "kind": "waiting_input", "evidence": [],
        "continuation": {"schema_version": 1, "kind": "awaiting_input", "reason": "ask"},
        "checkpoint": {"schema_version": 1, "facts": ["a fact"]},
    }

    result, narrative = parse_generic_result(json.dumps(candidate))

    assert not narrative
    assert result == candidate


@pytest.mark.parametrize("fields, error", [
    ({"unknown": True}, "invalid versioned value"),
    ({"evidence": {}}, "invalid Generic evidence list"),
    ({"evidence": [{}] * 41}, "invalid Generic evidence list"),
    ({"checkpoint": {"facts": ["x" * 16384]}}, "value exceeds size limit"),
])
def test_parse_without_transaction_enforces_envelope_bounds(fields, error):
    from work_domain.execution_result import parse_generic_result

    with pytest.raises(ValueError, match=error):
        parse_generic_result(json.dumps({"schema_version": 1, "kind": "progress_saved", **fields}))
