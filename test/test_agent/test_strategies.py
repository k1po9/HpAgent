"""Test WorkflowControlStrategy, ConditionEvaluator, ResultAggregator."""

import asyncio
import pytest
from src.agent.strategies import (
    ConditionEvaluator,
    ResultAggregator,
    WorkflowControlStrategy,
)
from src.agent.context import ExecutionContext
from src.agent.interfaces import MessageBus
from src.agent.types import (
    BatchOutcome,
    CapabilityRequirement,
    ExecutionPlan,
    Task,
    TaskResult,
    TaskStatus,
)


class TestConditionEvaluator:
    async def test_always(self):
        ev = ConditionEvaluator()
        assert await ev.evaluate("always", {}) is True

    async def test_never(self):
        ev = ConditionEvaluator()
        assert await ev.evaluate("never", {}) is False

    async def test_all_succeeded(self):
        ev = ConditionEvaluator()
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.COMPLETED),
        }
        assert await ev.evaluate("all_succeeded", results) is True

    async def test_all_succeeded_with_failure(self):
        ev = ConditionEvaluator()
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.COMPLETED),
            "b": TaskResult(task_id="b", status=TaskStatus.FAILED),
        }
        assert await ev.evaluate("all_succeeded", results) is False

    async def test_any_failed(self):
        ev = ConditionEvaluator()
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.COMPLETED),
            "b": TaskResult(task_id="b", status=TaskStatus.FAILED),
        }
        assert await ev.evaluate("any_failed", results) is True

    async def test_task_status_check(self):
        ev = ConditionEvaluator()
        results = {"t1": TaskResult(task_id="t1", status=TaskStatus.COMPLETED)}
        assert await ev.evaluate("task:t1.status==completed", results) is True
        assert await ev.evaluate("task:t1.status==failed", results) is False

    async def test_empty_predicate_returns_true(self):
        ev = ConditionEvaluator()
        assert await ev.evaluate("", {}) is True


class TestResultAggregator:
    async def test_concat(self):
        agg = ResultAggregator()
        results = {
            "a": TaskResult(task_id="a", output="hello"),
            "b": TaskResult(task_id="b", output="world"),
        }
        output = await agg.aggregate(results, strategy="concat")
        assert "hello" in output
        assert "world" in output

    async def test_first(self):
        agg = ResultAggregator()
        results = {
            "a": TaskResult(task_id="a", output="first"),
            "b": TaskResult(task_id="b", output="second"),
        }
        output = await agg.aggregate(results, strategy="first")
        assert output == "first"

    async def test_last(self):
        agg = ResultAggregator()
        results = {
            "a": TaskResult(task_id="a", output="first"),
            "b": TaskResult(task_id="b", output="second"),
        }
        output = await agg.aggregate(results, strategy="last")
        assert output == "second"

    async def test_merge(self):
        agg = ResultAggregator()
        results = {
            "a": TaskResult(task_id="a", output={"x": 1}),
            "b": TaskResult(task_id="b", output={"y": 2}),
        }
        output = await agg.aggregate(results, strategy="merge")
        assert output == {"x": 1, "y": 2}

    async def test_empty(self):
        agg = ResultAggregator()
        output = await agg.aggregate({})
        assert output is None


class TestWorkflowControlStrategy:
    async def test_initialize_plan_from_dag(self):
        t1 = Task(task_id="a", goal="first")
        t2 = Task(task_id="b", goal="second")
        strategy = WorkflowControlStrategy(
            dag_tasks={"a": t1, "b": t2},
            dag_dependencies={"b": ["a"]},
        )
        plan = await strategy.initialize_plan("test", ExecutionContext())
        assert len(plan.tasks) == 2
        assert plan.dependencies["b"] == ["a"]

    async def test_get_ready_batch_all_independent(self):
        t1 = Task(task_id="a", goal="first")
        t2 = Task(task_id="b", goal="second")
        strategy = WorkflowControlStrategy(
            dag_tasks={"a": t1, "b": t2},
            dag_dependencies={},
        )
        plan = await strategy.initialize_plan("test", ExecutionContext())
        pending = {"a", "b"}

        batch = await strategy.get_ready_batch(
            {}, plan, pending, None, ExecutionContext()
        )
        assert len(batch) == 2

    async def test_get_ready_batch_respects_dependencies(self):
        t1 = Task(task_id="a", goal="first")
        t2 = Task(task_id="b", goal="second")
        strategy = WorkflowControlStrategy(
            dag_tasks={"a": t1, "b": t2},
            dag_dependencies={"b": ["a"]},
        )
        plan = await strategy.initialize_plan("test", ExecutionContext())
        pending = {"a", "b"}

        # Before "a" completes, only "a" should be ready
        batch = await strategy.get_ready_batch(
            {}, plan, pending, None, ExecutionContext()
        )
        assert len(batch) == 1
        assert batch[0].task_id == "a"

    async def test_get_ready_batch_after_dep_completes(self):
        t1 = Task(task_id="a", goal="first")
        t2 = Task(task_id="b", goal="second")
        strategy = WorkflowControlStrategy(
            dag_tasks={"a": t1, "b": t2},
            dag_dependencies={"b": ["a"]},
        )
        plan = await strategy.initialize_plan("test", ExecutionContext())
        results = {"a": TaskResult(task_id="a", status=TaskStatus.COMPLETED)}
        pending = {"b"}

        batch = await strategy.get_ready_batch(
            results, plan, pending, None, ExecutionContext()
        )
        assert len(batch) == 1
        assert batch[0].task_id == "b"

    async def test_on_batch_completed_all_success(self):
        strategy = WorkflowControlStrategy()
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.COMPLETED),
        }
        outcome = await strategy.on_batch_completed(results, ExecutionPlan(), ExecutionContext())
        assert outcome.should_terminate is False
        assert len(outcome.failed_tasks_to_retry) == 0

    async def test_on_batch_completed_with_failure(self):
        strategy = WorkflowControlStrategy()
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.FAILED),
        }
        outcome = await strategy.on_batch_completed(results, ExecutionPlan(), ExecutionContext())
        # Failed task marked for retry
        assert "a" in outcome.failed_tasks_to_retry
