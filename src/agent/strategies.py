"""Control strategy implementations.

WorkflowControlStrategy — static DAG with condition branches.
SupervisorStrategy — LLM dynamic planning and review.
CouncilStrategy — parallel same-task execution with judge verdict.

Design decisions:
  - ConditionEvaluator evaluates predicate strings against batch results
  - ResultAggregator combines sub-results (for OrchestratorAsAgent)
  - LLM abstractions (Planner/Reviewer/Judge) are ABCs with stub implementations for Phase 2
  - Error handling via BatchOutcome (failed_tasks_to_retry, should_terminate)
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any, Awaitable, Callable

from .context import ExecutionContext
from .interfaces import ControlStrategy, MessageBus
from .types import (
    BatchOutcome,
    BranchCondition,
    CapabilityRequirement,
    ExecutionPlan,
    Task,
    TaskResult,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# ── LLM Call Protocol ──────────────────────────────────────────────────────────

CallLLM = Callable[[list[dict], list[dict] | None], Awaitable[str]]
"""A callable that sends messages to an LLM and returns text content.

Args:
    messages: list of {"role": str, "content": str} dicts
    tools: optional list of OpenAI-style tool definitions

Returns:
    The LLM's text response content.
"""


class ConditionEvaluator:
    """Evaluate branch condition predicates against completed results.

    Supported predicates:
      - "all_succeeded" — true when no FAILED results in the batch
      - "any_failed" — true when any result is FAILED
      - "task:<tid>.status==<status>" — check specific task status
      - "task:<tid>.output.<key>==<value>" — check specific output field
      - "always" — always true
      - "never" — always false
    """

    async def evaluate(
        self,
        predicate: str,
        results: dict[str, TaskResult],
        context: ExecutionContext | None = None,
    ) -> bool:
        if not predicate or predicate == "always":
            return True
        if predicate == "never":
            return False
        if predicate == "all_succeeded":
            return all(
                r.status != TaskStatus.FAILED for r in results.values()
            )
        if predicate == "any_failed":
            return any(
                r.status == TaskStatus.FAILED for r in results.values()
            )

        # "task:<tid>.status==<status>"
        if predicate.startswith("task:") and ".status==" in predicate:
            rest = predicate[len("task:"):]
            tid, status_str = rest.split(".status==", 1)
            if tid in results:
                return results[tid].status.value == status_str.strip()

        # "task:<tid>.output.<key>==<value>"
        if predicate.startswith("task:") and ".output." in predicate and "==" in predicate:
            rest = predicate[len("task:"):]
            parts = rest.split(".output.", 1)
            tid = parts[0]
            field_and_value = parts[1].split("==", 1)
            key = field_and_value[0].strip()
            expected = field_and_value[1].strip()
            if tid in results and isinstance(results[tid].output, dict):
                actual = str(results[tid].output.get(key, ""))
                return actual == expected

        return False

    def check_predicate_sync(
        self,
        predicate: str,
        source_task_id: str,
        results: dict[str, TaskResult],
    ) -> bool:
        """Synchronous predicate check for use in get_ready_batch.

        Limited to predicates that can be evaluated without I/O:
          - "always", "never", "all_succeeded", "any_failed"
          - "task:<tid>.status==<status>"
        """
        if not predicate or predicate == "always":
            return True
        if predicate == "never":
            return False
        if predicate == "all_succeeded":
            return all(r.status != TaskStatus.FAILED for r in results.values())
        if predicate == "any_failed":
            return any(r.status == TaskStatus.FAILED for r in results.values())

        # "task:<tid>.status==<status>"
        if predicate.startswith("task:") and ".status==" in predicate:
            rest = predicate[len("task:"):]
            tid, status_str = rest.split(".status==", 1)
            if tid in results:
                return results[tid].status.value == status_str.strip()

        # Unknown predicate → default to True (allow the task)
        return True


class ResultAggregator:
    """Aggregate sub-task results into a single output.

    Strategies:
      - "concat" — concatenate all output strings
      - "merge" — deep-merge all output dicts
      - "first" — take the first non-None result
      - "last" — take the last result
    """

    async def aggregate(
        self,
        results: dict[str, TaskResult],
        strategy: str = "concat",
        context: ExecutionContext | None = None,
    ) -> Any:
        items = list(results.values())

        if not items:
            return None

        if strategy == "first":
            for r in items:
                if r.output is not None:
                    return r.output
            return None

        if strategy == "last":
            for r in reversed(items):
                if r.output is not None:
                    return r.output
            return None

        if strategy == "concat":
            parts = []
            for r in items:
                if r.output is not None:
                    parts.append(str(r.output))
            return "\n".join(parts)

        if strategy == "merge":
            merged: dict = {}
            for r in items:
                if isinstance(r.output, dict):
                    merged.update(r.output)
            return merged

        # Default: return all results as a dict
        return {r.task_id: r.output for r in items}


class WorkflowControlStrategy(ControlStrategy):
    """Static DAG workflow strategy.

    Simplest control strategy — good for Phase 1 skeleton validation.
    Tasks and dependencies are pre-defined in the DAG config.
    """

    def __init__(
        self,
        dag_tasks: dict[str, Task] | None = None,
        dag_dependencies: dict[str, list[str]] | None = None,
        dag_branches: list[BranchCondition] | None = None,
        evaluator: ConditionEvaluator | None = None,
    ) -> None:
        self._tasks = dag_tasks or {}
        self._dependencies = dag_dependencies or {}
        self._branches = dag_branches or []
        self._evaluator = evaluator or ConditionEvaluator()
        self._retry_counts: dict[str, int] = {}

    async def initialize_plan(
        self, goal: str, context: ExecutionContext
    ) -> ExecutionPlan:
        """Build ExecutionPlan from pre-defined DAG config."""
        return ExecutionPlan(
            tasks=dict(self._tasks),
            dependencies=dict(self._dependencies),
            branches=list(self._branches),
        )

    async def get_ready_batch(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        pending: set[str],
        bus: MessageBus,
        context: ExecutionContext,
    ) -> list[Task]:
        """Return all tasks whose dependencies are satisfied and branch conditions pass."""
        ready: list[Task] = []

        for task_id in sorted(pending):
            task = plan.tasks.get(task_id)
            if task is None:
                continue

            # Check dependencies
            deps = plan.dependencies.get(task_id, [])
            if not self._dependencies_satisfied(deps, results):
                continue

            # Check branch conditions
            if not self._branch_allows(task_id, plan, results):
                continue

            ready.append(task)

        return ready

    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        context: ExecutionContext,
    ) -> BatchOutcome:
        """Check for failures and evaluate branch conditions.

        Failed tasks are retried up to context.config.max_retries.
        When all executed tasks fail and retries are exhausted, terminate.
        """
        outcome = BatchOutcome()
        max_retries = context.config.max_retries

        all_failed = True
        for tid, result in results.items():
            if result.status == TaskStatus.FAILED:
                count = self._retry_counts.get(tid, 0) + 1
                if count <= max_retries:
                    self._retry_counts[tid] = count
                    outcome.failed_tasks_to_retry.add(tid)
                    all_failed = False  # still trying
            elif result.status != TaskStatus.PENDING:
                all_failed = False

        if all_failed and results:
            outcome.should_terminate = True

        return outcome

    def _dependencies_satisfied(
        self, deps: list[str], results: dict[str, TaskResult]
    ) -> bool:
        """A task is ready when all its dependencies have a terminal status."""
        for dep_id in deps:
            if dep_id not in results:
                return False
            status = results[dep_id].status
            if status not in (
                TaskStatus.COMPLETED,
                TaskStatus.HANDED_OFF,
                TaskStatus.COMPENSATED,
            ):
                return False
        return True

    def _branch_allows(
        self,
        task_id: str,
        plan: ExecutionPlan,
        results: dict[str, TaskResult],
    ) -> bool:
        """Check branch conditions targeting this task.

        A task whose task_id matches branch.false_target is blocked when
        the branch condition evaluates to True (i.e., the true branch was taken).
        """
        for branch in plan.branches:
            # Block false_target if the condition is met (true_target was activated)
            if task_id == branch.false_target:
                if branch.source_task_id in results:
                    if branch.predicate and self._evaluator.check_predicate_sync(
                        branch.predicate, branch.source_task_id, results
                    ):
                        return False  # condition met → take true path, block false
            # Activate true_target if condition is met
            if task_id == branch.true_target:
                if branch.source_task_id in results:
                    if branch.predicate and not self._evaluator.check_predicate_sync(
                        branch.predicate, branch.source_task_id, results
                    ):
                        return False  # condition not met → block true path
        return True


# ── LLM Abstraction Interfaces ───────────────────────────────────────────────


class LLMPlanner(ABC):
    """LLM planner — decomposes a goal into tasks.

    Phase 2: StubLLMPlanner for testing.
    Phase 3: RealLLMPlanner wrapping ResourcePool.
    """

    @abstractmethod
    async def plan(
        self, goal: str, context: ExecutionContext
    ) -> tuple[list[Task], dict[str, list[str]]]:
        """Decompose goal into (tasks, dependencies)."""
        ...


class LLMReviewer(ABC):
    """LLM reviewer — reviews completed results and decides next steps.

    Phase 2: StubLLMReviewer for testing.
    Phase 3: RealLLMReviewer wrapping ResourcePool.
    """

    @abstractmethod
    async def review(
        self,
        completed: dict[str, TaskResult],
        context: ExecutionContext,
    ) -> tuple[bool, list[Task] | None]:
        """Review results. Returns (is_done, new_tasks_or_None)."""
        ...


class LLMJudge(ABC):
    """LLM judge — evaluates multiple results and returns a verdict.

    For Council mode: N agents produce N results, the judge picks the best.
    """

    @abstractmethod
    async def judge(
        self, results: dict[str, TaskResult], context: ExecutionContext
    ) -> Any:
        """Evaluate all results and return a verdict."""
        ...


# ── Stub LLM Implementations (Phase 2) ────────────────────────────────────────


class StubLLMPlanner(LLMPlanner):
    """Pre-programmed planner for testing.

    Returns a fixed task list based on the goal.
    """

    def __init__(
        self,
        task_map: dict[str, tuple[list[Task], dict[str, list[str]]]] | None = None,
    ) -> None:
        self._task_map = task_map or {}

    async def plan(
        self, goal: str, context: ExecutionContext
    ) -> tuple[list[Task], dict[str, list[str]]]:
        if goal in self._task_map:
            return self._task_map[goal]
        # Default: single task with the goal
        task = Task(
            task_id="auto_1",
            goal=goal,
            required_capability=CapabilityRequirement(required_tags={"default"}),
        )
        return [task], {}


class StubLLMReviewer(LLMReviewer):
    """Pre-programmed reviewer for testing.

    Returns (is_done, new_tasks) based on how many times it's been called.
    """

    def __init__(self, rounds: list[tuple[bool, list[Task] | None]] | None = None) -> None:
        self._rounds = rounds or [(True, None)]  # Default: done after 1 round
        self._call_count = 0

    async def review(
        self, completed: dict[str, TaskResult], context: ExecutionContext
    ) -> tuple[bool, list[Task] | None]:
        if self._call_count < len(self._rounds):
            is_done, new_tasks = self._rounds[self._call_count]
            self._call_count += 1
            return is_done, new_tasks
        return True, None


class MajorityJudge(LLMJudge):
    """Simple majority-vote judge (no LLM needed).

    Picks the result with the highest confidence, or majority vote.
    """

    async def judge(
        self, results: dict[str, TaskResult], context: ExecutionContext
    ) -> Any:
        successful = {
            tid: r for tid, r in results.items()
            if r.status == TaskStatus.COMPLETED and r.output is not None
        }
        if not successful:
            return {"verdict": "no_consensus", "reason": "all_failed"}

        # Count outputs (simple majority)
        outputs = [str(r.output) for r in successful.values()]
        counter = Counter(outputs)
        winner, count = counter.most_common(1)[0]

        return {
            "verdict": winner,
            "votes": count,
            "total": len(outputs),
            "results": {tid: r.output for tid, r in successful.items()},
        }


# ── Real LLM Implementations (Phase 3) ─────────────────────────────────────────


class RealLLMPlanner(LLMPlanner):
    """LLM-powered planner: decomposes a goal into Tasks via LLM call.

    Uses a call_llm function (dependency-injected) to generate a structured
    task plan from the goal description.

    The LLM is prompted to return a JSON object with "tasks" (array of
    {task_id, goal, required_tags, depends_on}) and optional "dependencies".
    """

    SYSTEM_PROMPT = (
        "You are a task-planning AI. Decompose the user's goal into concrete subtasks.\n"
        "Each subtask should be a discrete, independently executable unit of work.\n\n"
        "Output ONLY a JSON object (no markdown fences, no extra text) with this structure:\n"
        '{"tasks": [{"task_id": "string", "goal": "string", '
        '"required_tags": ["tag1"], "depends_on": ["task_id"]}]}\n\n'
        "Rules:\n"
        "- task_id must be unique and descriptive (e.g. \"research_topic\", \"write_code\")\n"
        "- goal describes what the task accomplishes\n"
        "- required_tags lists capability tags needed (e.g. \"code\", \"research\", \"review\")\n"
        "- depends_on lists task_ids that must complete first (empty list if none)\n"
        "- Order tasks logically; dependencies form a DAG\n"
        "- If the goal is simple, a single task is acceptable\n"
    )

    def __init__(
        self,
        call_llm: CallLLM,
        system_prompt: str | None = None,
        default_tags: set[str] | None = None,
    ) -> None:
        self._call_llm = call_llm
        self._system_prompt = system_prompt or self.SYSTEM_PROMPT
        self._default_tags = default_tags or {"default"}

    async def plan(
        self, goal: str, context: ExecutionContext
    ) -> tuple[list[Task], dict[str, list[str]]]:
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": f"Goal: {goal}"},
        ]

        try:
            raw = await self._call_llm(messages, None)
            data = self._parse_response(raw)
        except Exception as exc:
            logger.warning("LLM planner failed for goal %r: %s", goal, exc)
            # Fallback: single task
            task = Task(
                task_id="fallback_1",
                goal=goal,
                required_capability=CapabilityRequirement(
                    required_tags=self._default_tags
                ),
            )
            return [task], {}

        tasks: list[Task] = []
        deps: dict[str, list[str]] = {}
        for item in data.get("tasks", []):
            tid = item.get("task_id", f"task_{len(tasks)}")
            tags = set(item.get("required_tags", [])) or self._default_tags
            task = Task(
                task_id=tid,
                goal=item.get("goal", goal),
                required_capability=CapabilityRequirement(required_tags=tags),
            )
            tasks.append(task)
            task_deps = item.get("depends_on", [])
            if task_deps:
                deps[tid] = task_deps

        if not tasks:
            task = Task(
                task_id="fallback_1",
                goal=goal,
                required_capability=CapabilityRequirement(
                    required_tags=self._default_tags
                ),
            )
            tasks.append(task)

        return tasks, deps

    def _parse_response(self, raw: str) -> dict:
        """Extract JSON from LLM response, handling markdown fences."""
        text = raw.strip()
        if text.startswith("```"):
            # Strip markdown code fences
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)


class RealLLMReviewer(LLMReviewer):
    """LLM-powered reviewer: inspects completed results and decides next steps.

    Returns (is_done, new_tasks) — the reviewer can declare the goal achieved,
    or inject additional tasks to continue progress.
    """

    SYSTEM_PROMPT = (
        "You are a task-review AI. Evaluate completed task results and decide whether "
        "the overall goal is achieved or more work is needed.\n\n"
        "Output ONLY a JSON object (no markdown fences) with this structure:\n"
        '{"is_done": true/false, "reasoning": "brief", '
        '"new_tasks": [{"task_id": "string", "goal": "string", '
        '"required_tags": ["tag1"], "depends_on": ["task_id"]}]}\n\n'
        "Rules:\n"
        '- Set is_done=true when the goal is fully satisfied by the completed results\n'
        '- Set is_done=false and provide new_tasks to continue progress\n'
        "- new_tasks can be empty if you need more information before deciding\n"
        "- Each new task follows the same format: task_id, goal, required_tags, depends_on\n"
    )

    def __init__(
        self,
        call_llm: CallLLM,
        system_prompt: str | None = None,
        max_rounds: int = 10,
    ) -> None:
        self._call_llm = call_llm
        self._system_prompt = system_prompt or self.SYSTEM_PROMPT
        self._max_rounds = max_rounds
        self._round = 0

    async def review(
        self, completed: dict[str, TaskResult], context: ExecutionContext
    ) -> tuple[bool, list[Task] | None]:
        self._round += 1
        if self._round >= self._max_rounds:
            logger.warning("Reviewer hit max rounds (%d), forcing done", self._max_rounds)
            return True, None

        # Summarize completed results for the LLM
        summary_lines = []
        for tid, result in completed.items():
            status = result.status.value
            output_summary = (
                str(result.output)[:200] if result.output else "(no output)"
            )
            error_msg = result.error.message if result.error else ""
            summary_lines.append(
                f"- {tid}: {status}"
                + (f" → {output_summary}" if result.output else "")
                + (f" [error: {error_msg}]" if error_msg else "")
            )
        summary = "\n".join(summary_lines)

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": f"Completed tasks:\n{summary}"},
        ]

        try:
            raw = await self._call_llm(messages, None)
            data = self._parse_response(raw)
        except Exception as exc:
            logger.warning("LLM reviewer failed: %s, assuming done", exc)
            return True, None

        is_done = data.get("is_done", True)
        new_tasks: list[Task] | None = None
        if not is_done and "new_tasks" in data:
            new_tasks = []
            for item in data["new_tasks"]:
                new_tasks.append(Task(
                    task_id=item.get("task_id", f"review_{len(new_tasks)}"),
                    goal=item.get("goal", ""),
                    required_capability=CapabilityRequirement(
                        required_tags=set(item.get("required_tags", ["default"]))
                    ),
                ))

        return is_done, new_tasks

    def _parse_response(self, raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)


class RealLLMJudge(LLMJudge):
    """LLM-powered judge: evaluates multiple agent outputs and selects the best.

    For Council mode: N agents produce N outputs, the judge picks or synthesizes
    the best answer.
    """

    SYSTEM_PROMPT = (
        "You are a judging AI. Evaluate multiple candidate outputs for the same task "
        "and select or synthesize the best answer.\n\n"
        "Output ONLY a JSON object (no markdown fences) with this structure:\n"
        '{"verdict": "the chosen or synthesized answer", '
        '"reasoning": "why this was chosen", "confidence": 0.0-1.0}\n\n'
        "Rules:\n"
        "- If one answer is clearly best, select it as the verdict\n"
        "- If answers are complementary, synthesize them into a better answer\n"
        "- If all answers disagree and none is clearly best, set verdict to \"no_consensus\"\n"
        "- confidence indicates how certain you are (1.0 = unanimous agreement)\n"
    )

    def __init__(
        self,
        call_llm: CallLLM,
        system_prompt: str | None = None,
    ) -> None:
        self._call_llm = call_llm
        self._system_prompt = system_prompt or self.SYSTEM_PROMPT

    async def judge(
        self, results: dict[str, TaskResult], context: ExecutionContext
    ) -> Any:
        successful = {
            tid: r for tid, r in results.items()
            if r.status == TaskStatus.COMPLETED and r.output is not None
        }
        if not successful:
            return {"verdict": "no_consensus", "reason": "all_failed"}

        # Present each candidate to the LLM
        candidates_text = []
        for i, (tid, result) in enumerate(successful.items(), 1):
            candidates_text.append(
                f"Candidate {i} ({tid}): {result.output}"
            )
        candidates_block = "\n".join(candidates_text)

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": f"Evaluate these candidates:\n\n{candidates_block}"},
        ]

        try:
            raw = await self._call_llm(messages, None)
            data = self._parse_response(raw)
        except Exception as exc:
            logger.warning("LLM judge failed: %s, falling back to majority", exc)
            return await MajorityJudge().judge(results, context)

        return {
            "verdict": data.get("verdict", "no_consensus"),
            "reasoning": data.get("reasoning", ""),
            "confidence": data.get("confidence", 0.0),
            "results": {tid: r.output for tid, r in successful.items()},
        }

    def _parse_response(self, raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)


class SupervisorControlStrategy(ControlStrategy):
    """Supervisor mode: LLM dynamically decomposes goal → reviews → continues/terminates.

    Flow:
      1. initialize_plan: Planner LLM generates initial tasks
      2. get_ready_batch: returns all dependency-satisfied tasks
      3. on_batch_completed: Reviewer LLM checks results → new_tasks or terminate
    """

    def __init__(
        self,
        planner: LLMPlanner,
        reviewer: LLMReviewer | None = None,
    ) -> None:
        self._planner = planner
        self._reviewer = reviewer

    async def initialize_plan(
        self, goal: str, context: ExecutionContext
    ) -> ExecutionPlan:
        """Call planner to decompose the goal."""
        tasks_list, deps = await self._planner.plan(goal, context)
        tasks = {t.task_id: t for t in tasks_list}
        return ExecutionPlan(tasks=tasks, dependencies=deps)

    async def get_ready_batch(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        pending: set[str],
        bus: MessageBus,
        context: ExecutionContext,
    ) -> list[Task]:
        """Return all tasks whose dependencies are satisfied."""
        ready: list[Task] = []

        for task_id in sorted(pending):
            task = plan.tasks.get(task_id)
            if task is None:
                continue

            deps = plan.dependencies.get(task_id, [])
            all_deps_met = True
            for dep_id in deps:
                if dep_id not in results:
                    all_deps_met = False
                    break
                status = results[dep_id].status
                if status not in (
                    TaskStatus.COMPLETED,
                    TaskStatus.HANDED_OFF,
                    TaskStatus.COMPENSATED,
                ):
                    all_deps_met = False
                    break

            if all_deps_met:
                ready.append(task)

        return ready

    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        context: ExecutionContext,
    ) -> BatchOutcome:
        """Review results with LLM → decide continue or terminate."""
        if self._reviewer is None:
            return BatchOutcome()

        is_done, new_tasks = await self._reviewer.review(results, context)

        outcome = BatchOutcome()
        if is_done:
            outcome.should_terminate = True
        if new_tasks:
            outcome.new_tasks = list(new_tasks)

        return outcome


# ── Council Strategy ─────────────────────────────────────────────────────────


class CouncilControlStrategy(ControlStrategy):
    """Council mode: same goal executed by N agents in parallel, then judge decides.

    Flow:
      1. initialize_plan: create N identical-goal tasks for N agents
      2. get_ready_batch: return all N tasks
      3. on_batch_completed: judge LLM reviews all results → injects verdict
    """

    def __init__(
        self,
        agent_capabilities: list[CapabilityRequirement],
        judge: LLMJudge,
        council_name: str = "council",
    ) -> None:
        self._agent_capabilities = agent_capabilities
        self._judge = judge
        self._council_name = council_name

    async def initialize_plan(
        self, goal: str, context: ExecutionContext
    ) -> ExecutionPlan:
        """Create N identical-goal tasks, one per agent capability."""
        tasks: dict[str, Task] = {}
        for i, cap in enumerate(self._agent_capabilities):
            task_id = f"{self._council_name}_{i}"
            tasks[task_id] = Task(
                task_id=task_id,
                goal=goal,
                required_capability=cap,
                task_type="council_vote",
            )
        return ExecutionPlan(tasks=tasks)

    async def get_ready_batch(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        pending: set[str],
        bus: MessageBus,
        context: ExecutionContext,
    ) -> list[Task]:
        """Return all council tasks (no dependencies between them)."""
        ready: list[Task] = []
        for task_id in sorted(pending):
            task = plan.tasks.get(task_id)
            if task is not None:
                ready.append(task)
        return ready

    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        context: ExecutionContext,
    ) -> BatchOutcome:
        """Judge all results and inject the verdict as an injected result."""
        # Only judge council tasks (filter by task_type)
        council_results = {
            tid: r for tid, r in results.items()
            if plan.tasks.get(tid) and plan.tasks[tid].task_type == "council_vote"
        }

        if not council_results:
            return BatchOutcome(should_terminate=True)

        verdict = await self._judge.judge(council_results, context)

        # Determine if the verdict represents success or failure
        status = TaskStatus.COMPLETED
        if isinstance(verdict, dict) and verdict.get("verdict") == "no_consensus":
            status = TaskStatus.FAILED

        return BatchOutcome(
            injected_results={
                f"{self._council_name}_verdict": TaskResult(
                    task_id=f"{self._council_name}_verdict",
                    status=status,
                    output=verdict,
                ),
            },
            should_terminate=True,
        )
