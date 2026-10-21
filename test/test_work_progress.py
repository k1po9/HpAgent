"""Semantic Research comparison excludes presentation and producer identities."""

from copy import deepcopy

from work_domain.progress import research_facts


def test_research_semantics_ignore_random_ids_and_order_but_keep_source_changes():
    snapshot = {
        "run_id": "first",
        "invocation_id": "first",
        "claims": [
            {
                "claim_id": "a",
                "statement": "A verified fact",
                "importance": "important",
                "evidence": [
                    {
                        "evidence_id": "e1",
                        "source_id": "s1",
                        "canonical_uri": "https://example.test/a",
                        "content_hash": "aaa",
                    }
                ],
            },
            {"claim_id": "b", "statement": "Another fact", "importance": "normal", "evidence": []},
        ],
    }
    equivalent = deepcopy(snapshot)
    equivalent.update(run_id="second", invocation_id="second")
    equivalent["claims"][0].update(claim_id="new-id", statement="A  VERIFIED fact")
    equivalent["claims"][0]["evidence"][0].update(
        evidence_id="new-evidence", source_id="new-source"
    )
    equivalent["claims"].reverse()
    equivalent["claims"].append(deepcopy(equivalent["claims"][0]))
    assert research_facts(snapshot) == research_facts(equivalent)
    equivalent["claims"][1]["evidence"][0]["content_hash"] = "changed"
    assert research_facts(snapshot) != research_facts(equivalent)
