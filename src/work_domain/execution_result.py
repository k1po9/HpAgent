"""Interpret Generic Work results independently of execution context loading."""

import json
import re

from work_domain.models import GENERIC_RESULT_EVIDENCE_TYPES, bounded, continuation


def _generic_result_object(content):
    """Accept one explicit JSON result; prose alone never supplies a result."""
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except ValueError:
        # Providers can wrap the explicit final object despite the raw-JSON
        # instruction. Exactly one final JSON fence is unambiguous; do not scan
        # arbitrary prose/braces or choose among multiple candidate blocks.
        blocks = list(re.finditer(r"(?m)^```json[ \t]*\r?\n([\s\S]*?)^```[ \t]*$", content))
        if len(blocks) != 1 or content[blocks[0].end():].strip() or content.count("```") != 2:
            return None
        try:
            return json.loads(blocks[0].group(1))
        except ValueError:
            return None


def parse_generic_result(content: object) -> tuple[dict, bool]:
    """Parse the bounded recommendation without consulting persistent facts.

    The boolean records whether the input was narrative, so only that fallback
    may later be promoted using trusted observations. Explicit waits stay explicit.
    """
    result = _generic_result_object(content)
    narrative = not isinstance(result, dict) or result.get("schema_version") != 1 or "kind" not in result
    if narrative:
        # Narrative is retained as a candidate, never interpreted as completion evidence.
        result = {
            "schema_version": 1,
            "kind": "progress_saved",
            "evidence": [],
            "continuation": continuation("awaiting_input", "result_requires_acceptance"),
        }
    bounded(result, {"schema_version", "kind", "evidence", "continuation", "checkpoint"})
    evidence_items = result.get("evidence", [])
    if not isinstance(evidence_items, list) or len(evidence_items) > 40:
        raise ValueError("invalid Generic evidence list")
    return result, narrative


def generic_result(uow, run, work, content, result_ref):
    """Resolve a recommendation against facts in the caller's existing transaction.

    This does not open or commit a UnitOfWork, finalize the Run, or complete Work.
    The lifecycle owner passes the same transaction on to completion policy.
    """
    result, narrative = parse_generic_result(content)
    evidence_items = result.get("evidence", [])
    for evidence in evidence_items:
        if not isinstance(evidence, dict):
            raise ValueError("invalid Generic evidence")
        if evidence.get("type") not in GENERIC_RESULT_EVIDENCE_TYPES:
            raise ValueError("Generic completion needs typed result evidence, not arbitrary successful tools")
        from run_domain.acceptance import validate_typed_evidence
        validate_typed_evidence(uow, run, evidence)
    from work_domain.output_evidence import observed_text_evidence

    automatic = observed_text_evidence(uow, run)
    for item in automatic:
        if not any(e['criterion_id'] == item['criterion_id'] for e in evidence_items):
            from run_domain.acceptance import validate_typed_evidence

            validate_typed_evidence(uow, run, item)
            evidence_items.append(item)
    if len(evidence_items) > 40:
        raise ValueError("invalid Generic evidence list")
    result['evidence'] = evidence_items
    if narrative and automatic:
        from work_domain.integration import criteria_satisfied

        requirement = run['input_snapshot']['requirement']
        from work_domain.deliverables import validate_delivery_evidence
        try:
            validate_delivery_evidence(uow, run, requirement, evidence_items)
            delivery_valid = True
        except ValueError:
            delivery_valid = False
        if (delivery_valid and criteria_satisfied(requirement['acceptance_criteria'], evidence_items)
                and not requirement['deliverable_policy'].get('required')):
            result['kind'] = 'deliverable_ready'
            result['continuation'] = continuation('ready', 'observed_output_evidence')
    from run_domain.delegation import required_branch_gaps
    gaps = required_branch_gaps(uow, run["account_id"], run["run_id"])
    if gaps and result["kind"] == "deliverable_ready":
        result["kind"] = "progress_saved"
        result["continuation"] = continuation("blocked", "required_branch_missing:" + ",".join(gaps))
    if "checkpoint" not in result:
        result["checkpoint"] = {
            "schema_version": 1,
            "checkpoint_version": work["checkpoint"]["checkpoint_version"] + 1,
            "requirement_revision": run["requirement_revision"],
            "source_run_id": str(run["run_id"]),
            "facts": [],
            "decisions": [],
            "unresolved": [],
            "next_steps": [],
            "evidence_refs": [result_ref],
        }
    suggest_completion = result["kind"] == "deliverable_ready"
    criteria = run["input_snapshot"].get("requirement", {}).get("acceptance_criteria", [])
    if suggest_completion and any(c["required"] and "user_acceptance" in c["evidence_types"] for c in criteria):
        result["continuation"] = continuation("awaiting_input", "user_acceptance_required")
        suggest_completion = False
    return result, suggest_completion
