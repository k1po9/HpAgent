"""多Agent架构核心数据结构。

设计原则（经过5轮架构评审）:
  - Task 描述「做什么」—— 不含编排依赖，是纯任务单元
  - ExecutionPlan 描述「怎么编排」—— 含依赖图、条件分支等调度信息
  - BatchOutcome 是策略层唯一的决策输出通道 —— 所有策略决策都通过它流向编排器
  - Handoff 是 Agent 行为层的概念，由 TaskResult.handoff_request 承载

数据流:
  Strategy(决策) → BatchOutcome → Orchestrator(机械执行) → Task → Agent.execute() → TaskResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── 枚举类型 ──────────────────────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    """任务状态 —— 描述任务在生命周期中的当前阶段。

    状态流转路径:
      PENDING → IN_PROGRESS → COMPLETED / FAILED / HANDED_OFF
      COMPENSATING → COMPENSATED（补偿路径，用于失败后的回滚/清理）
    """
    PENDING = "pending"             # 等待分配 Agent 执行
    IN_PROGRESS = "in_progress"     # Agent 正在执行中
    COMPLETED = "completed"         # 成功完成
    FAILED = "failed"               # 执行失败（可能触发重试或补偿）
    HANDED_OFF = "handed_off"       # Agent 完成当前任务并要求将控制权转交另一个 Agent
    COMPENSATING = "compensating"   # 补偿操作执行中（回滚/清理）
    COMPENSATED = "compensated"     # 补偿操作已完成


class AgentHealth(str, Enum):
    """Agent 健康状态 —— 影响调度时的匹配优先级。

    DEGRADED 的 Agent 仅在 allow_degraded=True 时才会被匹配。
    UNHEALTHY 的 Agent 不会被调度到任何新任务。
    """
    HEALTHY = "healthy"       # 正常，优先匹配
    DEGRADED = "degraded"     # 降级但仍可用（如限流、部分功能不可用）
    UNHEALTHY = "unhealthy"   # 不健康，不可调度


# ── 能力模型 ──────────────────────────────────────────────────────────────────────


@dataclass
class CapabilitySpec:
    """Agent 侧的能力声明 —— 描述一个 Agent「能做什么」。

    Attributes:
        tags: 能力标签集合（如 {"code", "python", "debug"}）。用集合而非单一标签，
              支持多维匹配 —— 一个 Agent 可以同时具备多种能力。
        priority: 调度优先级（数值越大优先级越高）。当多个 Agent 都匹配时，
                  Orchestrator 优先选择 priority 高的。
        cost_tier: 成本等级（"default" / "premium"），用于成本控制 ——
                   低成本任务不会调度到 premium Agent。
    """
    tags: set[str] = field(default_factory=set)
    priority: int = 0
    cost_tier: str = "default"


@dataclass
class CapabilityRequirement:
    """任务侧的能力需求 —— 描述一个 Task「需要什么样的 Agent 来执行」。

    Attributes:
        required_tags: 必须匹配的能力标签。Agent 的 CapabilitySpec.tags 必须包含所有这些标签。
        min_priority: 最低优先级要求。只有 priority >= min_priority 的 Agent 才会被考虑。
        max_cost: 最大成本容忍度。只有 cost_tier <= max_cost 的 Agent 才会被选中。
        allow_degraded: 是否允许使用降级状态的 Agent。默认 False 表示只用健康 Agent。
    """
    required_tags: set[str] = field(default_factory=set)
    min_priority: int = 0
    max_cost: str = "premium"
    allow_degraded: bool = False


# ── 任务与结果 ────────────────────────────────────────────────────────────────────


@dataclass
class HandoffRequest:
    """转交请求 —— Agent 声明将控制权转交给另一个 Agent。

    与「任务拆分」（由 Planner 规划）不同，Handoff 是 Agent 在运行时主动发起的
    控制权转移。典型场景:
      - Agent A 完成初步分析后认为需要 Agent B 的专业能力
      - 当前 Agent 发现自己不适合继续处理，建议转交

    Attributes:
        target_capability: 目标 Agent 的能力需求描述（用于匹配）
        context_to_pass: 传递给下一个 Agent 的上下文数据（如中间结果、状态）
        reason: 转交原因（用于日志/审计追踪）
    """
    target_capability: CapabilityRequirement = field(default_factory=CapabilityRequirement)
    context_to_pass: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class ErrorInfo:
    """结构化错误信息 —— 提供比纯字符串更丰富的诊断数据。

    Attributes:
        type: 错误类型标识（如 "TimeoutError", "ToolExecutionError"），用于分类处理。
        message: 人类可读的错误描述。
        retryable: 是否可重试。True 时 Orchestrator 可根据策略决定是否重试。
        partial_output: 失败前的部分产出（如部分生成的代码），用于诊断和恢复。
    """
    type: str = ""
    message: str = ""
    retryable: bool = False
    partial_output: Any = None


@dataclass
class ExecutionMetrics:
    """执行指标 —— 单次任务执行的度量数据。

    Attributes:
        duration_ms: 执行耗时（毫秒）
        token_usage: Token 用量（格式: {"prompt": N, "completion": M, "total": T}）
    """
    duration_ms: float = 0.0
    token_usage: dict = field(default_factory=dict)


@dataclass
class Task:
    """纯任务单元 —— 描述「要做什么」，不含任何编排依赖。

    Task 与 ExecutionPlan 严格分离:
      - Task 只关心工作内容（goal）+ 能力需求（required_capability）+ 输入数据
      - ExecutionPlan 关心依赖关系、分支、执行顺序

    Attributes:
        task_id: 任务唯一标识（建议格式: "task-{short-uuid}"）
        goal: 任务目标，用自然语言描述要完成什么（Agent 用此生成执行计划）
        required_capability: 执行此任务需要的能力要求
        input_data: 输入数据（上下文、历史消息、记忆等），由 HarnessRunner 注入
        task_type: 任务类型标签，用于补偿注册表查找对应的补偿逻辑
        parent_task_id: 父任务 ID，用于追踪子任务关系（子任务失败时级联处理）
    """
    task_id: str = ""
    goal: str = ""
    required_capability: CapabilityRequirement = field(default_factory=CapabilityRequirement)
    input_data: dict = field(default_factory=dict)
    task_type: str = "default"
    parent_task_id: Optional[str] = None


@dataclass
class TaskResult:
    """任务执行结果 —— Agent 完成（或失败）后的产出。

    Attributes:
        task_id: 对应的 Task ID
        status: 最终状态（COMPLETED / FAILED / HANDED_OFF）
        output: 成功时的产出（可以是字符串、字典等任意类型）
        error: 失败时的结构化错误信息（status == FAILED 时必填）
        metrics: 执行指标（耗时、Token 用量等），用于成本追踪和性能分析
        handoff_request: 转交请求（status == HANDED_OFF 时必填）
        trace_id: 执行追踪 ID，关联到日志/审计系统
    """
    task_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    output: Any = None
    error: Optional[ErrorInfo] = None
    metrics: Optional[ExecutionMetrics] = None
    handoff_request: Optional[HandoffRequest] = None
    trace_id: Optional[str] = None


# ── 编排计划 ──────────────────────────────────────────────────────────────────────


@dataclass
class BranchCondition:
    """条件分支 —— ExecutionPlan 中的分支点。

    由 ConditionEvaluator 在运行时评估 predicate，根据结果选择不同路径。
    典型场景:
      - "如果初审通过 → 自动执行，否则 → 人工审核"
      - "如果置信度 > 0.9 → 跳过复审"

    Attributes:
        source_task_id: 分支源任务 ID（此任务完成后评估分支条件）
        predicate: 条件表达式字符串（如 "all_succeeded", "any_failed", "task.x.status==completed"）
        true_target: predicate 为 True 时跳转到的任务 ID
        false_target: predicate 为 False 时跳转到的任务 ID（None 表示终止该路径）
    """
    source_task_id: str = ""
    predicate: str = ""
    true_target: str = ""
    false_target: Optional[str] = None


@dataclass
class ExecutionPlan:
    """编排层任务图 —— 描述任务间的依赖关系和分支逻辑。

    与 Task 完全解耦:
      - Task 是可执行的工作单元（由 Agent 消费）
      - ExecutionPlan 是调度蓝图（由 Orchestrator 消费）

    Attributes:
        tasks: 任务字典（task_id → Task），包含所有待执行的任务实例
        dependencies: 依赖图（task_id → [依赖的 task_id 列表]）。
                      一个任务只有在其所有依赖项都 COMPLETED 后才会被调度。
        branches: 条件分支列表，在源任务完成后评估决定执行路径
    """
    tasks: dict[str, Task] = field(default_factory=dict)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    branches: list[BranchCondition] = field(default_factory=list)


# ── 策略反馈通道 ──────────────────────────────────────────────────────────────────


@dataclass
class BatchOutcome:
    """策略决策输出 —— 多Agent架构中策略层 → 编排层的唯一通信通道。

    设计理念:
      - Strategy（有策略判断能力）产出一个 BatchOutcome
      - Orchestrator（纯机械执行，无策略判断）消费 BatchOutcome 并原样执行
      - 这样的单向数据流确保职责清晰：策略做决策，编排器做执行

    每个字段的含义及 Orchestrator 的对应动作:
      - injected_results: 直接注入结果字典的条目（如 Council 的裁决结果），不经过 Agent 执行
      - new_tasks: 动态注入的新任务（如 Supervisor 在 review 后决定新增的步骤），
                   添加到 ExecutionPlan 并标记为 PENDING
      - tasks_to_remove: 从等待队列中移除的任务 ID 集合（如 Reviewer 判定某个步骤不需要执行）
      - failed_tasks_to_retry: 需要重试的失败任务 ID 集合，重新加入等待队列
      - should_terminate: 是否终止当前轮次的执行循环（如发现不可恢复的错误、目标已完成等）
    """
    injected_results: dict[str, TaskResult] = field(default_factory=dict)
    new_tasks: list[Task] = field(default_factory=list)
    tasks_to_remove: set[str] = field(default_factory=set)
    failed_tasks_to_retry: set[str] = field(default_factory=set)
    should_terminate: bool = False
