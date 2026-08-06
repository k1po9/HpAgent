"""Offline-only frozen History replay gate for ``WebRunWorkflow`` (D-09).

This module is the historical-compatibility gate.  It replays a Workflow
History that was captured from a real Temporal Execution and frozen in the
repository, using the *current* Workflow code, so that any future change that
breaks determinism of already-shipped executions fails CI here.

It intentionally connects to no Temporal Server, no PostgreSQL, starts no
Docker, depends on ``TEMPORAL_HOST`` and no other test producing files, and
never modifies the fixture — it runs in a fresh checkout as a pure unit gate.

Do NOT add ``pytest.mark.temporal`` or ``pytest.mark.postgres`` to this test,
and never regenerate or overwrite the fixture from a test or CI step.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from orchestration.web_workflow import WebRunWorkflow

# Captured from a real completed happy-path Execution; see
# test/fixtures/temporal/README.md for provenance and update rules.
FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "temporal"
    / "web_run_workflow_v1_completed.json"
)
FROZEN_WORKFLOW_ID = "hpagent-web-run-a423e6b8-17b8-4446-8ed2-873152ae8329"
EXPECTED_ACTIVITIES = ("prepare_run_activity", "execute_agent_activity")
_CLOSE_EVENT_KINDS = {
    "WORKFLOW_EXECUTION_COMPLETED",
    "WORKFLOW_EXECUTION_FAILED",
    "WORKFLOW_EXECUTION_CANCELED",
    "WORKFLOW_EXECUTION_TIMED_OUT",
    "WORKFLOW_EXECUTION_TERMINATED",
    "WORKFLOW_EXECUTION_CONTINUED_AS_NEW",
}


def _kind(event_type: object) -> str:
    """Normalize a proto3 JSON ``eventType`` to the short enum suffix.

    ``History.to_json()`` renders enums in the long form (for example
    ``EVENT_TYPE_WORKFLOW_EXECUTION_STARTED``) while Temporal UI/CLI dumps use
    the short form (``WORKFLOW_EXECUTION_STARTED``).  ``WorkflowHistory.from_json``
    accepts both; this helper makes the fixture assertions tolerant of both.
    """
    return str(event_type).removeprefix("EVENT_TYPE_")


def _first_payload(payload_container: dict) -> dict:
    """Decode the single json/plain payload produced by the default converter."""
    payloads = payload_container["payloads"]
    assert len(payloads) == 1, "expected exactly one payload"
    encoded = payloads[0]["data"]
    decoded = base64.b64decode(encoded).decode("utf-8")
    return json.loads(decoded)


async def test_web_run_workflow_replays_frozen_v1_completed_history():
    # 1. The fixture must exist, parse as JSON, and carry a real History.
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    history = json.loads(raw)
    events = history.get("events")
    assert isinstance(events, list) and events, "History must contain events"

    # 2. The first key event is Workflow Execution Started, for WebRunWorkflow.
    first_kind = _kind(events[0]["eventType"])
    assert first_kind == "WORKFLOW_EXECUTION_STARTED", (
        f"History must begin with Workflow Execution Started, got {first_kind}"
    )
    started_attrs = events[0]["workflowExecutionStartedEventAttributes"]
    assert started_attrs["workflowType"]["name"] == "WebRunWorkflow"

    # 3. The expected Activities were scheduled by the Workflow.
    scheduled = [
        event["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for event in events
        if _kind(event["eventType"]) == "ACTIVITY_TASK_SCHEDULED"
        and "activityTaskScheduledEventAttributes" in event
    ]
    for expected in EXPECTED_ACTIVITIES:
        assert expected in scheduled, f"scheduled Activities must include {expected}"

    # 4. The History is closed normally and is a completed happy path.
    close_events = [
        event for event in events if _kind(event["eventType"]) in _CLOSE_EVENT_KINDS
    ]
    assert len(close_events) == 1, "History must have exactly one close event"
    close_kind = _kind(close_events[0]["eventType"])
    assert close_kind == "WORKFLOW_EXECUTION_COMPLETED", (
        f"frozen fixture must be a completed happy path, got {close_kind}"
    )
    completed_attrs = close_events[0]["workflowExecutionCompletedEventAttributes"]
    result = _first_payload(completed_attrs["result"])
    assert result["outcome"] == "completed"

    # 5. Input-minimality guard: the Started payload carries only the frozen
    #    {schema_version, run_id} contract — no business data.
    payload = _first_payload(started_attrs["input"])
    assert set(payload) == {"run_id", "schema_version"}
    assert payload["schema_version"] == 1

    # 6. The current Workflow code must replay the frozen History exactly.
    workflow_history = WorkflowHistory.from_json(FROZEN_WORKFLOW_ID, raw)
    replay = await Replayer(workflows=[WebRunWorkflow]).replay_workflow(
        workflow_history
    )
    assert replay.replay_failure is None, f"frozen History replay failed: {replay.replay_failure}"
