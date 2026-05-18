"""HpAgent Multi-Agent Architecture.

Core components (from 5-round architecture review):

  Agent is action  |  Task is data  |  Orchestrator is scheduling
  Strategy is policy  |  Bus is communication

Four orchestration modes via ControlStrategy:
  - Supervisor: LLM dynamically generates plan
  - Workflow: static DAG + condition branches
  - Council: parallel same-task + verdict
  - Handoff: Agent behavior (not a separate Strategy)

Recursive composition: OrchestratorAsAgent wraps any Orchestrator as an Agent.
"""

from .adapters import ReActAgent
from .bus import InMemoryMessageBus
from .compensation import CompensationHandler, CompensationRegistry
from .composite import OrchestratorAsAgent
from .context import ExecutionContext, RuntimeConfig, SessionState, SharedMemory
from .factory import (
    ResourcePoolAdapter,
    build_council,
    build_supervisor,
    build_workflow,
)
from .interfaces import (
    AgentRegistry,
    BaseAgent,
    ControlStrategy,
    MessageBus,
)
from .llm_agent import LLMAgent
from .orchestrator import Orchestrator
from .registry import InMemoryAgentRegistry
from .runner import MultiAgentExecutor
from .strategies import (
    CallLLM,
    ConditionEvaluator,
    CouncilControlStrategy,
    LLMJudge,
    LLMPlanner,
    LLMReviewer,
    MajorityJudge,
    RealLLMJudge,
    RealLLMPlanner,
    RealLLMReviewer,
    ResultAggregator,
    StubLLMPlanner,
    StubLLMReviewer,
    SupervisorControlStrategy,
    WorkflowControlStrategy,
)
from .types import (
    AgentHealth,
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

__all__ = [
    # ── Types ──
    "Task",
    "TaskResult",
    "TaskStatus",
    "AgentHealth",
    "ErrorInfo",
    "ExecutionMetrics",
    "HandoffRequest",
    "CapabilitySpec",
    "CapabilityRequirement",
    "ExecutionPlan",
    "BranchCondition",
    "BatchOutcome",
    # ── Interfaces ──
    "BaseAgent",
    "ControlStrategy",
    "AgentRegistry",
    "MessageBus",
    # ── Context ──
    "ExecutionContext",
    "SessionState",
    "SharedMemory",
    "RuntimeConfig",
    # ── Orchestrator ──
    "Orchestrator",
    # ── Strategies ──
    "WorkflowControlStrategy",
    "SupervisorControlStrategy",
    "CouncilControlStrategy",
    "ConditionEvaluator",
    "ResultAggregator",
    # ── LLM Abstractions ──
    "CallLLM",
    "LLMPlanner",
    "LLMReviewer",
    "LLMJudge",
    "StubLLMPlanner",
    "StubLLMReviewer",
    "MajorityJudge",
    "RealLLMPlanner",
    "RealLLMReviewer",
    "RealLLMJudge",
    # ── Infrastructure ──
    "InMemoryMessageBus",
    "InMemoryAgentRegistry",
    "CompensationRegistry",
    "CompensationHandler",
    # ── Factory ──
    "ResourcePoolAdapter",
    "build_supervisor",
    "build_council",
    "build_workflow",
    # ── Agents ──
    "LLMAgent",
    "MultiAgentExecutor",
    # ── Adapters ──
    "ReActAgent",
    "OrchestratorAsAgent",
]
