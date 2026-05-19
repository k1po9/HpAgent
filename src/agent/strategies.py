"""控制策略实现。

WorkflowControlStrategy —— 静态 DAG，带条件分支。
SupervisorStrategy —— 基于 LLM 的动态规划与审查。
CouncilStrategy —— 并行同任务执行并由裁决者判定。

设计决策：
    - ConditionEvaluator 对批次结果评估谓词字符串
    - ResultAggregator 合并子结果（用于 OrchestratorAsAgent）
    - LLM 抽象（Planner/Reviewer/Judge）为 ABC，Phase 2 有 stub 实现
    - 错误处理通过 BatchOutcome（failed_tasks_to_retry、should_terminate）完成
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

CallLLM = Callable[[list[dict], list[dict] | None], Awaitable[Any]]
"""向 LLM 发送消息并返回响应对象的可调用对象。

参数：
    messages：由 {"role": str, "content": str} 构成的消息列表
    tools：可选的工具定义列表

返回：
    具有 content (str | None) 和 tool_calls (list | None) 属性的响应对象。
"""


class ConditionEvaluator:
    """根据已完成结果评估分支条件谓词。

    支持的谓词：
        - "all_succeeded" — 当批次中不存在 FAILED 结果时为真
        - "any_failed" — 当任一结果为 FAILED 时为真
        - "task:<tid>.status==<status>" — 检查特定任务状态
        - "task:<tid>.output.<key>==<value>" — 检查特定输出字段
        - "always" — 恒真
        - "never" — 恒假
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
        """用于 get_ready_batch 的同步谓词检查。

        限制为无需 I/O 即可评估的谓词：
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
    """将子任务结果聚合为单一输出。

    策略：
        - "concat" — 连接所有输出字符串
        - "merge" — 合并所有输出字典
        - "first" — 取第一个非 None 的结果
        - "last" — 取最后一个结果
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
    """静态 DAG 的工作流策略。

    最简单的控制策略——适合 Phase 1 的骨架验证。
    任务和依赖在 DAG 配置中预先定义。
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
        """从预定义的 DAG 配置构建 ExecutionPlan。"""
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
        """返回所有其依赖已满足且分支条件通过的任务。"""
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
        """检查失败并评估分支条件。

        失败的任务会重试，最多到 context.config.max_retries。
        当所有已执行任务失败且重试耗尽时，终止执行。
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
        """当所有依赖都处于终态时，任务即被视为就绪。"""
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
        """检查指向该任务的分支条件。

        当分支条件为真（即选择了 true 分支）时，匹配 branch.false_target 的任务会被阻塞。
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
    """LLM 规划器——将目标分解为任务。"""

    @abstractmethod
    async def plan(
        self, goal: str, context: ExecutionContext, agent_tags: set[str] | None = None
    ) -> tuple[list[Task], dict[str, list[str]]]:
        """将目标分解为 (tasks, dependencies)。"""
        ...


class LLMReviewer(ABC):
    """LLM 审查器——审阅已完成的结果并决定下一步。"""

    @abstractmethod
    async def review(
        self,
        completed: dict[str, TaskResult],
        context: ExecutionContext,
    ) -> tuple[bool, list[Task] | None]:
        """审查结果。返回 (is_done, new_tasks_or_None)。"""
        ...


class LLMJudge(ABC):
    """LLM 裁决者——评估多个结果并返回裁决。

    在 Council 模式下：N 个代理产生 N 个结果，裁决者选择最佳者。
    """

    @abstractmethod
    async def judge(
        self, results: dict[str, TaskResult], context: ExecutionContext
    ) -> Any:
        """评估所有结果并返回裁决。"""
        ...


class StubLLMPlanner(LLMPlanner):
    """预设的规划器（用于测试）。

    根据目标返回固定的任务列表。
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
    """预设的审查器（用于测试）。

    根据被调用次数返回 (is_done, new_tasks)。
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
    """简单多数投票裁决器（无需 LLM）。

    选择置信度最高或获得多数票的结果。
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
    """基于 LLM 的规划器：通过工具调用（Tool Calling）将目标分解为任务。

    LLM 通过多次调用 add_task 工具来声明每个子任务，
    而不是一次性输出 JSON，从而获得更稳定、结构化的任务规划。
    """

    SYSTEM_PROMPT = (
        "You are a task-planning AI. Decompose the user's goal into concrete subtasks.\n"
        "Each subtask should be a discrete, independently executable unit of work.\n\n"
        "Use the add_task tool to declare each subtask. Call it once per subtask.\n\n"
        "Rules:\n"
        "- task_id must be unique and descriptive (e.g. \"research_topic\", \"write_code\")\n"
        "- goal describes what the task accomplishes\n"
        "- required_tags lists capability tags needed (e.g. \"code\", \"research\", \"review\")\n"
        "- depends_on lists task_ids that must complete first (empty list if none)\n"
        "- Call add_task for tasks in dependency order (dependencies first)\n"
        "- If the goal is simple, a single add_task call is acceptable\n"
    )

    ADD_TASK_TOOL: list[dict] = [{
        "name": "add_task",
        "description": "Declare a subtask in the execution plan. Call once per subtask.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Unique descriptive identifier (e.g. 'research_topic', 'write_code')",
                },
                "goal": {
                    "type": "string",
                    "description": "What this task should accomplish",
                },
                "required_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capability tags needed",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that must complete before this one",
                },
            },
            "required": ["task_id", "goal"],
        },
    }]

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
        self, goal: str, context: ExecutionContext, agent_tags: set[str] | None = None
    ) -> tuple[list[Task], dict[str, list[str]]]:
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": f"Goal: {goal}, and the tags available are: {', '.join(agent_tags) if agent_tags else 'default'}."},
        ]

        try:
            response = await self._call_llm(messages, self.ADD_TASK_TOOL)
        except Exception as exc:
            logger.warning("LLM planner failed for goal %r: %s", goal, exc)
            return self._fallback_task(goal)

        tool_calls = getattr(response, "tool_calls", None) or []

        if tool_calls:
            return self._tasks_from_tool_calls(tool_calls, goal)

        # Fallback: 从文字回答中解析
        content = getattr(response, "content", None)
        if content:
            try:
                data = self._parse_json_response(content)
                return self._tasks_from_dict(data, goal)
            except Exception as exc:
                logger.warning("LLM planner JSON fallback failed: %s", exc)

        return self._fallback_task(goal)

    def _tasks_from_tool_calls(
        self, tool_calls: list, goal: str
    ) -> tuple[list[Task], dict[str, list[str]]]:
        tasks: list[Task] = []
        deps: dict[str, list[str]] = {}
        for tc in tool_calls:
            name = getattr(tc, "name", "") or tc.get("name", "")
            if name != "add_task":
                continue
            args = getattr(tc, "arguments", {}) or tc.get("arguments", {})
            tid = args.get("task_id", f"task_{len(tasks)}")
            tags = set(args.get("required_tags", [])) or self._default_tags
            task = Task(
                task_id=tid,
                goal=args.get("goal", goal),
                required_capability=CapabilityRequirement(required_tags=tags),
            )
            tasks.append(task)
            task_deps = args.get("depends_on", [])
            if task_deps:
                deps[tid] = task_deps

        if not tasks:
            return self._fallback_task(goal)
        return tasks, deps

    def _tasks_from_dict(
        self, data: dict, goal: str
    ) -> tuple[list[Task], dict[str, list[str]]]:
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
            return self._fallback_task(goal)
        return tasks, deps

    def _fallback_task(self, goal: str) -> tuple[list[Task], dict[str, list[str]]]:
        task = Task(
            task_id="fallback_1",
            goal=goal,
            required_capability=CapabilityRequirement(
                required_tags=self._default_tags
            ),
        )
        return [task], {}

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)


class RealLLMReviewer(LLMReviewer):
    """基于 LLM 的审查器：审视已完成的结果并决定下一步。

    返回 (is_done, new_tasks) —— 审查器可声明目标已达成，或注入额外任务以继续推进。
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

        # 为 LLM 汇总已完成的结果
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
            response = await self._call_llm(messages, None)
            data = self._parse_response(response.content or "")
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
    """基于 LLM 的裁决者：评估多个代理输出并选择最佳结果。

    在 Council 模式下：N 个代理产生 N 个输出，裁决者选择或综合出最佳答案。
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

        # 向 LLM 展示每个候选结果
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
            response = await self._call_llm(messages, None)
            data = self._parse_response(response.content or "")
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
    """Supervisor 模式：LLM 动态分解目标 → 审查 → 继续或终止。

    流程：
        1. initialize_plan：Planner LLM 生成初始任务
        2. get_ready_batch：返回所有依赖满足的任务
        3. on_batch_completed：Reviewer LLM 检查结果 → 新任务或终止
    """

    def __init__(
        self,
        planner: LLMPlanner,
        reviewer: LLMReviewer | None = None,
    ) -> None:
        self._planner = planner
        self._reviewer = reviewer

    async def initialize_plan(
        self, goal: str, context: ExecutionContext, agent_tags: set[str] | None = None
    ) -> ExecutionPlan:
        """调用LLMPlanner进行任务规划."""
        tasks_list, deps = await self._planner.plan(goal, context, agent_tags)
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
        """类比有向无环图，返回入度为0的节点(无上游依赖的任务)."""
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
    """Council 模式：相同目标由 N 个代理并行执行，随后裁决者决定结果。

    流程：
        1. initialize_plan：为 N 个代理创建 N 个相同目标的任务
        2. get_ready_batch：返回所有 N 个任务
        3. on_batch_completed：裁决 LLM 审查所有结果 → 注入裁决
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
