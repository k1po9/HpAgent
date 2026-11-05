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
