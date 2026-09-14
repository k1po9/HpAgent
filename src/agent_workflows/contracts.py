"""Compact, versioned contracts crossing durable Workflow boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AGENT_SCHEMA_VERSION = 3
AGENT_TASK_QUEUE = "hpagent-web-agent"
AGENT_STRATEGY_REACT = "react"
AGENT_STRATEGY_PLAN = "plan_and_execute"
AGENT_STRATEGIES = frozenset({AGENT_STRATEGY_REACT, AGENT_STRATEGY_PLAN})
DURABLE_TOOL_ACTIVITY_START_TO_CLOSE_SECONDS = 600
DURABLE_LEASE_SAFETY_MARGIN_SECONDS = 60


@dataclass(frozen=True)
class RunSource:
    """Source owner reference; account/run identity remains lifecycle authority."""

    source_kind: str
    source_ref: str

    def __post_init__(self) -> None:
        if not self.source_kind or not self.source_ref:
            raise ValueError("Run source kind and reference are required")


@dataclass(frozen=True)
class ChatContext:
    """Conversation-owned context, required only by Chat adapters."""

    conversation_id: str
    session_id: str
    trigger_message_id: str | None = None

    def __post_init__(self) -> None:
        if not self.conversation_id or not self.session_id:
            raise ValueError("Chat context requires Conversation and Session")


@dataclass(frozen=True)
class RunContext:
    context_ref: str | None = None
    chat: ChatContext | None = None
    surface: str | None = None

    def require_chat(self) -> ChatContext:
        """Explicit boundary of the current Chat context/resource adapter."""
        if self.chat is None:
            raise ValueError("Current Chat adapter requires Chat context")
        return self.chat


@dataclass(frozen=True, kw_only=True)
class AgentRunInput:
    schema_version: int
    run_id: str
    account_id: str
    source: RunSource
    context: RunContext
    strategy: str
    max_turns: int = 20


@dataclass(frozen=True)
class ContextBootstrapInput:
    schema_version: int
    run_id: str
    account_id: str
    source: RunSource
    context: RunContext
    strategy: str
    operation_id: str

    execution_attempt: int = field(default=1, kw_only=True)
    lease_token: int = field(default=0, kw_only=True)


@dataclass(frozen=True)
class ContextBootstrapResult:
    schema_version: int
    transcript_id: str
    transcript_version: int
    context_ref: str


@dataclass(frozen=True)
class CompactToolCall:
    tool_call_id: str
    name: str
    arguments_ref: str


@dataclass(frozen=True)
class ModelDecisionInput:
    schema_version: int
    run_id: str
    account_id: str
    source: RunSource
    context: RunContext
    strategy: str
    transcript_id: str
    transcript_version: int
    turn: int
    operation_id: str
    lease_token: int
    objective: str | None = None
    final_only: bool = False
    plan_id: str | None = None
    plan_version: int | None = None
    step_id: str | None = None

    execution_attempt: int = field(default=1, kw_only=True)


@dataclass(frozen=True)
class ModelDecisionResult:
    schema_version: int
    operation_id: str
    decision_type: Literal["final", "tool_calls"]
    decision_ref: str
    tool_calls: tuple[CompactToolCall, ...]
    transcript_version: int
    stop_reason: str
    display_summary: str = ""


@dataclass(frozen=True)
class ToolExecutionInput:
    schema_version: int
    run_id: str
    account_id: str
    source: RunSource
    context: RunContext
    strategy: str
    transcript_id: str
    transcript_version: int
    turn: int
    operation_id: str
    lease_token: int
    tool_call: CompactToolCall
    plan_id: str | None = None
    plan_version: int | None = None
    step_id: str | None = None

    execution_attempt: int = field(default=1, kw_only=True)


@dataclass(frozen=True)
class ToolExecutionResult:
    schema_version: int
    operation_id: str
    result_ref: str
    transcript_version: int
    display_summary: str
    approval_id: str | None = None
    approval_status: str = "not_required"
    approval_expires_at: str | None = None
    tool_success: bool | None = None


@dataclass(frozen=True)
class ApprovalStatusInput:
    schema_version: int
    account_id: str
    run_id: str
    operation_id: str
    approval_id: str


@dataclass(frozen=True)
class ApprovalStatusResult:
    schema_version: int
    approval_id: str
    operation_id: str
    status: Literal["pending", "approved", "rejected", "expired", "cancelled"]


@dataclass(frozen=True)
class ApprovalDecisionSignal:
    schema_version: int
    approval_id: str
    operation_id: str


@dataclass(frozen=True)
class ApprovedToolExecutionInput:
    schema_version: int
    account_id: str
    run_id: str
    operation_id: str
    lease_token: int
    approval_id: str
    transcript_id: str
    transcript_version: int
    tool_call_id: str
    tool_name: str

    execution_attempt: int = field(default=1, kw_only=True)


@dataclass(frozen=True)
class ApprovedToolExecutionResult:
    schema_version: int
    operation_id: str
    result_ref: str
    display_summary: str
    transcript_version: int


@dataclass(frozen=True)
class AgentResult:
    schema_version: int
    run_id: str
    result_ref: str
    transcript_id: str
    transcript_version: int
    tool_turns: int


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    ordinal: int
    title: str
    objective: str


@dataclass(frozen=True)
class PlanningInput:
    schema_version: int
    run_id: str
    account_id: str
    source: RunSource
    context: RunContext
    transcript_id: str
    transcript_version: int
    operation_id: str
    lease_token: int
    plan_id: str
    plan_version: int
    previous_plan_ref: str | None = None
    previous_plan_version: int | None = None
    completed_step_refs: tuple[str, ...] = ()
    trigger_step_id: str | None = None
    evaluation_reason: str | None = None

    execution_attempt: int = field(default=1, kw_only=True)


@dataclass(frozen=True)
class PlanningResult:
    schema_version: int
    operation_id: str
    plan_id: str
    plan_version: int
    steps: tuple[PlanStep, ...]
    transcript_version: int


@dataclass(frozen=True)
class PlanEvaluationInput:
    schema_version: int
    run_id: str
    account_id: str
    source: RunSource
    context: RunContext
    transcript_id: str
    transcript_version: int
    operation_id: str
    lease_token: int
    plan_id: str
    plan_version: int
    step_id: str
    step_index: int
    step_count: int

    execution_attempt: int = field(default=1, kw_only=True)


@dataclass(frozen=True)
class PlanEvaluationResult:
    schema_version: int
    operation_id: str
    decision: Literal["continue", "replan", "complete", "fail"]
    reason: str


@dataclass(frozen=True)
class AgentStepInput:
    schema_version: int
    agent: AgentRunInput
    transcript_id: str
    transcript_version: int
    plan_id: str
    plan_version: int
    step: PlanStep
    max_turns: int = 8


@dataclass(frozen=True)
class AgentStepResult:
    schema_version: int
    step_id: str
    result_ref: str
    transcript_version: int
    tool_turns: int


@dataclass(frozen=True)
class FinalizeResultInput:
    schema_version: int
    run_id: str
    result_ref: str


def strategy_for_profile(profile: str) -> str:
    normalized = profile.strip().casefold()
    if normalized in {"web_plan", "plan", AGENT_STRATEGY_PLAN}:
        return AGENT_STRATEGY_PLAN
    if normalized in {"web_chat", "chat", AGENT_STRATEGY_REACT}:
        return AGENT_STRATEGY_REACT
    return normalized
