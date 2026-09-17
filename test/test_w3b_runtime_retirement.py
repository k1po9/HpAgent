"""Production composition must remain executable after physical runtime removal."""
import sys
from dataclasses import fields
from pathlib import Path

from orchestration.config import TemporalConfig
from orchestration.worker import WorkerDependencies


def test_actual_production_registries_match_current_runtime():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "check"))
    try:
        from temporal_registry import scan
        report = scan()
    finally:
        sys.path.pop(0)
    assert report["pass"], report
    registries = {item["task_queue"]: item for item in report["captured_registries"]}
    assert len(registries) == 4
    assert registries["hpagent-web-agent"]["workflows"] == [
        "AgentRunWorkflow", "ReactAgentWorkflow", "PlanAndExecuteWorkflow",
        "AgentStepWorkflow", "ToolExecutionWorkflow",
    ]
    assert registries["hpagent-task-queue"]["workflows"] == [
        "ReflectWorkflow", "MetricsReportWorkflow",
    ]
    assert report["composition"]["reminder_handlers"] == ["user_reminder"]
    assert report["composition"]["run_dispatcher"] == "WebOutboxDispatcher"
    assert report["composition"]["artifact_dispatcher"] == "ArtifactOutboxDispatcher"


def test_dead_host_slots_and_whole_turn_activity_timeouts_are_removed():
    assert {field.name for field in fields(WorkerDependencies)}.isdisjoint({
        "qq_execution_host", "session_archive",
    })
    assert {field.name for field in fields(TemporalConfig)}.isdisjoint({
        "durable_agent_enabled", "web_agent_schedule_to_close_seconds",
        "web_agent_start_to_close_seconds", "web_agent_heartbeat_timeout_seconds",
        "web_cancel_cleanup_timeout_seconds",
    })
    # This queue is still active for scheduled memory maintenance.
    assert TemporalConfig().task_queue == "hpagent-task-queue"


def test_removed_lazy_export_is_not_a_runtime_escape_hatch():
    import orchestration

    assert "OrchestrationWorkflow" not in orchestration.__all__
    assert not hasattr(orchestration, "OrchestrationWorkflow")
