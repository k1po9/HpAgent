from __future__ import annotations

import ast
import inspect
from uuid import UUID

import pytest

from orchestration.research_workflow import (
    RESEARCH_WORKFLOW_SCHEMA_VERSION,
    ResearchIterationInput,
    ResearchReportWorkflow,
    ResearchWorkflowInput,
)
from orchestration.web_dispatcher import StartDecision, TemporalOutboxDispatcher


def test_research_workflow_input_and_history_payload_are_compact():
    assert list(ResearchWorkflowInput.__dataclass_fields__) == ["schema_version", "run_id"]
    assert list(ResearchIterationInput.__dataclass_fields__) == [
        "schema_version",
        "run_id",
        "iteration",
    ]
    ResearchWorkflowInput(RESEARCH_WORKFLOW_SCHEMA_VERSION, "run-1").validate()
    source = inspect.getsource(ResearchReportWorkflow)
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "execute_activity" not in names  # attribute calls only
    for forbidden in (
        "SourceCandidate",
        "SourceContent",
        "EvidenceItem",
        "psycopg",
        "httpx",
        "trafilatura",
    ):
        assert forbidden not in source
    for activity_name in (
        "prepare_research_activity",
        "create_research_plan_activity",
        "discover_research_sources_activity",
        "rank_research_sources_activity",
        "fetch_research_sources_activity",
        "normalize_research_sources_activity",
        "extract_research_evidence_activity",
        "assess_research_corroboration_activity",
        "analyze_research_gaps_activity",
        "synthesize_research_report_activity",
        "verify_research_citations_activity",
        "compare_previous_research_activity",
        "publish_research_artifact_activity",
        "complete_research_activity",
    ):
        assert activity_name in source


@pytest.mark.asyncio
async def test_research_outbox_dispatch_uses_persisted_deterministic_workflow_id():
    run_id = UUID("00000000-0000-0000-0000-000000000123")

    class Store:
        recorded = None

        def prepare_start(self, value):
            return StartDecision(value, f"hpagent-research-{value}", True)

        def still_queued(self, value):
            return True

        def record_started(self, value, temporal_run_id):
            self.recorded = (value, temporal_run_id)

    class Temporal:
        async def start_research_run(self, workflow_id, request):
            assert workflow_id == f"hpagent-research-{run_id}"
            assert request == ResearchWorkflowInput(1, str(run_id))
            return "temporal-run-id"

    store = Store()
    dispatcher = TemporalOutboxDispatcher(store, Temporal())
    assert await dispatcher.dispatch_research_start(run_id)
    assert store.recorded == (run_id, "temporal-run-id")
