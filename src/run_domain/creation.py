"""Pure input construction; SQL, authorization and budget checks stay with the caller."""

from common.agent_policy import AgentPolicy


def build_work_input_snapshot(work, requirement, wakeup_id, strategy, agent_policy=None) -> dict:
    snapshot = {
        "schema_version": 1,
        "requirement_revision": requirement["revision"],
        "requirement": {
            key: requirement[key]
            for key in (
                "objective",
                "capability_key",
                "spec",
                "constraints",
                "acceptance_criteria",
                "completion_mode",
                "timing",
                "resource_requests",
                "deliverable_policy",
            )
        },
        "checkpoint": work["checkpoint"],
        "wakeup_id": str(wakeup_id),
    }
    if strategy == "generic_agent":
        snapshot["agent_policy"] = (agent_policy or AgentPolicy()).to_snapshot()
    if requirement.get("deliverables") is not None:
        snapshot["requirement"]["deliverables"] = requirement["deliverables"]
    return snapshot


def build_work_budget_limits(strategy: str, executor: str) -> dict:
    research = executor == "research_report"
    limits = (
        {
            "sources_discovered": 30,
            "source_fetches": 20,
            "research_iterations": 3,
            "model_input_tokens": 80000,
            "model_output_tokens": 20000,
            "model_total_tokens": 100000,
            "model_calls": 20,
            "tool_calls": 40,
            "wall_time_ms": 1800000,
        }
        if research
        else (
            {
                "model_input_tokens": 80000,
                "model_output_tokens": 20000,
                "model_total_tokens": 100000,
                "model_calls": 40,
                "tool_calls": 40,
            }
            if strategy in {"generic_agent", "fixed_workflow"}
            else {"tool_calls": 40}
        )
    )
    if strategy in {"generic_agent", "fixed_workflow"}:
        limits.update(
            {
                "bytes_scanned": 1024 * 1024 * 1024,
                "bytes_returned_to_model": 2 * 1024 * 1024,
                "bytes_written": 256 * 1024 * 1024,
                "output_file_bytes": 256 * 1024 * 1024,
            }
        )
    # Local Research shares the producing Run's ledger, including Generic Work.
    for dimension, amount in {
        "sources_discovered": 30,
        "source_fetches": 20,
        "research_iterations": 3,
    }.items():
        limits.setdefault(dimension, amount)
    limits.setdefault("wall_time_ms", 600000)
    return limits


from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, kw_only=True)
class ChatRunInsert:
    run_id: UUID
    account_id: UUID
    conversation_id: UUID
    session_id: UUID | None
    trigger_message_id: UUID
    workflow_id: str
    context_message_seq: int
    retry_of_run_id: UUID | None
    strategy_kind: str
    executor_key: str
    agent_strategy: str | None
    input_snapshot: dict[str, Any]
    executor_version: int = 1
    strategy_policy_version: int = 1


@dataclass(frozen=True, kw_only=True)
class WorkRunInsert:
    run_id: UUID
    account_id: UUID
    work_id: UUID
    requirement_revision: int
    work_control_epoch: int
    wakeup_id: UUID
    workflow_id: str
    strategy_kind: str
    executor_key: str
    agent_strategy: str | None
    retry_of_run_id: UUID | None
    executor_version: int
    strategy_policy_version: int
    input_snapshot: dict[str, Any]


def build_chat_run_insert(
    run_id: UUID,
    account_id: UUID,
    conversation_id: UUID,
    session_id: UUID | None,
    trigger_message_id: UUID,
    workflow_id: str,
    context_message_seq: int,
    retry_of_run_id: UUID | None = None,
    agent_strategy: str = "react",
    fixed_html: dict[str, Any] | None = None,
    agent_policy: dict[str, Any] | None = None,
) -> ChatRunInsert:
    # Preserve legacy truthiness, NULL, defaults and exact snapshot shape.
    return ChatRunInsert(
        run_id=run_id,
        account_id=account_id,
        conversation_id=conversation_id,
        session_id=session_id,
        trigger_message_id=trigger_message_id,
        workflow_id=workflow_id,
        context_message_seq=context_message_seq,
        retry_of_run_id=retry_of_run_id,
        agent_strategy=None if fixed_html else agent_strategy,
        strategy_kind="fixed_workflow" if fixed_html else "generic_agent",
        executor_key="artifact_html" if fixed_html else "chat_agent",
        input_snapshot=fixed_html
        or {
            "schema_version": 1,
            "main_protocol": "tool_first_v1",
            **({"agent_policy": agent_policy} if agent_policy is not None else {}),
        },
    )
