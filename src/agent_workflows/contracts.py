"""Compact, versioned contracts crossing durable Workflow boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AGENT_SCHEMA_VERSION = 1
AGENT_TASK_QUEUE = "hpagent-web-agent"
AGENT_STRATEGY_REACT = "react"
AGENT_STRATEGY_PLAN = "plan_and_execute"
AGENT_STRATEGIES = frozenset({AGENT_STRATEGY_REACT, AGENT_STRATEGY_PLAN})


@dataclass(frozen=True)
class AgentRunInput:
    schema_version: int
    run_id: str
    account_id: str
    conversation_id: str
    session_id: str
    strategy: str
    trigger_message_id: str | None
    lease_token: int
    interaction_profile: str = "web_chat"
    max_turns: int = 20


@dataclass(frozen=True)
class ContextBootstrapInput:
    schema_version: int
    run_id: str
    account_id: str
    conversation_id: str
    session_id: str
    strategy: str
    operation_id: str


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
    conversation_id: str
    session_id: str
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
    conversation_id: str
    session_id: str
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


@dataclass(frozen=True)
class ToolExecutionResult:
    schema_version: int
    operation_id: str
    result_ref: str
    transcript_version: int
    display_summary: str


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
    conversation_id: str
    session_id: str
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
    conversation_id: str
    session_id: str
    transcript_id: str
    transcript_version: int
    operation_id: str
    lease_token: int
    plan_id: str
    plan_version: int
    step_id: str
    step_index: int
    step_count: int


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
