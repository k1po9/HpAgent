"""Test core data structures."""

import pytest
from src.agent.types import (
    BatchOutcome,
    BranchCondition,
    CapabilityRequirement,
    CapabilitySpec,
    ErrorInfo,
    ExecutionMetrics,
    ExecutionPlan,
    HandoffRequest,
    Task,
    TaskResult,
    TaskStatus,
)


class TestTask:
    def test_default_construction(self):
        t = Task()
        assert t.task_id == ""
        assert t.goal == ""
        assert t.task_type == "default"
        assert t.parent_task_id is None

    def test_full_construction(self):
        req = CapabilityRequirement(required_tags={"code", "python"})
        t = Task(
            task_id="task_1",
            goal="Write a function",
            required_capability=req,
            input_data={"lang": "python"},
            task_type="code_gen",
            parent_task_id="parent_1",
        )
        assert t.task_id == "task_1"
        assert t.required_capability.required_tags == {"code", "python"}
        assert t.input_data["lang"] == "python"
        assert t.task_type == "code_gen"
        assert t.parent_task_id == "parent_1"


class TestTaskStatus:
    def test_all_statuses_exist(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.HANDED_OFF.value == "handed_off"
        assert TaskStatus.COMPENSATING.value == "compensating"
        assert TaskStatus.COMPENSATED.value == "compensated"

    def test_handed_off_is_separate_from_completed(self):
        assert TaskStatus.HANDED_OFF != TaskStatus.COMPLETED


class TestHandoffRequest:
    def test_default(self):
        hr = HandoffRequest()
        assert hr.reason == ""

    def test_full(self):
        req = CapabilityRequirement(required_tags={"review"})
        hr = HandoffRequest(
            target_capability=req,
            context_to_pass={"key": "val"},
            reason="Need review",
        )
        assert hr.target_capability.required_tags == {"review"}
        assert hr.context_to_pass == {"key": "val"}
        assert hr.reason == "Need review"


class TestTaskResult:
    def test_failed_task_cannot_handoff(self):
        """Handoff is only valid from non-FAILED tasks (enforced by Orchestrator)."""
        hr = HandoffRequest(reason="test")
        result = TaskResult(
            task_id="t1",
            status=TaskStatus.FAILED,
            handoff_request=hr,
        )
        assert result.status == TaskStatus.FAILED
        assert result.handoff_request is not None

    def test_handed_off_status_explicit(self):
        result = TaskResult(
            task_id="t1",
            status=TaskStatus.HANDED_OFF,
            handoff_request=HandoffRequest(reason="delegating"),
        )
        assert result.status == TaskStatus.HANDED_OFF

    def test_trace_id_propagation(self):
        result = TaskResult(task_id="t1", status=TaskStatus.COMPLETED, trace_id="trace_123")
        assert result.trace_id == "trace_123"


class TestErrorInfo:
    def test_structured_error(self):
        err = ErrorInfo(
            type="TimeoutError",
            message="timed out",
            retryable=True,
            partial_output="partial",
        )
        assert err.retryable is True
        assert err.type == "TimeoutError"
        assert err.partial_output == "partial"


class TestCapabilityMatching:
    def test_spec_and_requirement_match(self):
        spec = CapabilitySpec(tags={"code", "python"}, priority=5)
        req = CapabilityRequirement(required_tags={"code"}, min_priority=3)
        assert req.required_tags.issubset(spec.tags)
        assert spec.priority >= req.min_priority

    def test_spec_insufficient_tags(self):
        spec = CapabilitySpec(tags={"code"})
        req = CapabilityRequirement(required_tags={"code", "review"})
        assert not req.required_tags.issubset(spec.tags)

    def test_spec_insufficient_priority(self):
        spec = CapabilitySpec(tags={"code"}, priority=1)
        req = CapabilityRequirement(required_tags={"code"}, min_priority=5)
        assert spec.priority < req.min_priority


class TestExecutionPlan:
    def test_default(self):
        plan = ExecutionPlan()
        assert plan.tasks == {}
        assert plan.dependencies == {}
        assert plan.branches == []

    def test_with_tasks_and_deps(self):
        t1 = Task(task_id="a", goal="first")
        t2 = Task(task_id="b", goal="second")
        plan = ExecutionPlan(
            tasks={"a": t1, "b": t2},
            dependencies={"b": ["a"]},
        )
        assert "a" in plan.tasks
        assert plan.dependencies["b"] == ["a"]


class TestBranchCondition:
    def test_branch_condition(self):
        bc = BranchCondition(
            source_task_id="t1",
            predicate="task:t1.output.score>0.8",
            true_target="t2",
            false_target="t3",
        )
        assert bc.source_task_id == "t1"
        assert bc.true_target == "t2"
        assert bc.false_target == "t3"


class TestBatchOutcome:
    def test_default_noop(self):
        bo = BatchOutcome()
        assert bo.injected_results == {}
        assert bo.new_tasks == []
        assert bo.tasks_to_remove == set()
        assert bo.failed_tasks_to_retry == set()
        assert bo.should_terminate is False

    def test_termination(self):
        bo = BatchOutcome(should_terminate=True)
        assert bo.should_terminate is True

    def test_new_tasks_injection(self):
        t = Task(task_id="new_1", goal="dynamic task")
        bo = BatchOutcome(new_tasks=[t])
        assert len(bo.new_tasks) == 1
        assert bo.new_tasks[0].task_id == "new_1"


class TestExecutionMetrics:
    def test_defaults(self):
        m = ExecutionMetrics()
        assert m.duration_ms == 0.0
        assert m.token_usage == {}
