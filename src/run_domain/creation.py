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
