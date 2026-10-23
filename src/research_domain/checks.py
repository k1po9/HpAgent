"""Bounded basic checks: source identity/location, structure and required outputs only."""


def basic_checks(report, citations, claims):
    structured = report["report_structured_json"]
    gaps = list(structured.get("gaps", []))
    if not report["report_markdown"].strip() or not structured.get("title") or not claims:
        gaps.append(
            {"code": "invalid_structure", "message": "Report title, body and claims are required."}
        )
    for claim in claims:
        linked = [c for c in citations if c["claim_id"] == claim["claim_id"]]
        expected = structured.get("claims", [])[claim["ordinal"] - 1].get("evidence_ids", [])
        if not linked or len(linked) != len(set(expected)):
            gaps.append(
                {"code": "missing_citation", "message": "A claim cites unavailable evidence."}
            )
            break
    if any(c["verification_status"] in {"invalid", "pending"} for c in citations):
        gaps.append(
            {
                "code": "invalid_citation",
                "message": "Citation identity or source location did not verify.",
            }
        )
    return {
        "schema_version": 1,
        "verifier": "research.basic",
        "version": "1",
        "scope": ["nonempty_structure", "citation_identity_and_location", "required_outputs"],
        "passed": not gaps,
        "gaps": gaps,
        "warnings": ["weak_sources"]
        if any(c["verification_status"] == "weak_source" for c in citations)
        else [],
        "not_checked": ["factual_truth", "browser_functionality"],
    }
