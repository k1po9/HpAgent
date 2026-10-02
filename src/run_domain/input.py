"""Load durable Agent identity from the Run and its root Execution."""

from temporalio.exceptions import ApplicationError

from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AgentRunInput,
    ChatContext,
    RunContext,
    RunSource,
)


class RunInputLoader:
    def __init__(self, store, *, max_turns: int = 20, surface: str = "web"):
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        self.store = store
        self.max_turns = max_turns
        self.surface = surface

    def load(self, run_id: str) -> AgentRunInput:
        identity = self.store.run_identity(run_id)
        if identity["status"] not in {"queued", "running"}:
            raise ApplicationError(
                "Run cannot execute", type="run_not_executable", non_retryable=True
            )
        if identity["strategy_kind"] != "generic_agent":
            raise ApplicationError("Run does not use the Agent executor", non_retryable=True)
        chat = None
        if identity["source_kind"] == "chat":
            if not identity["conversation_id"] or not identity["trigger_message_id"]:
                raise ApplicationError("Chat source context unavailable", non_retryable=True)
            chat = ChatContext(
                identity["conversation_id"], trigger_message_id=identity["trigger_message_id"]
            )
        return AgentRunInput(
            schema_version=AGENT_SCHEMA_VERSION,
            run_id=run_id,
            execution_id=identity["execution_id"],
            account_id=identity["account_id"],
            strategy=identity["agent_strategy"] or "react",
            max_turns=self.max_turns,
            source=RunSource(
                identity["source_kind"], identity["conversation_id"] or identity["work_id"]
            ),
            context=RunContext(
                chat=chat,
                context_ref=f"execution:{identity['execution_id']}",
                surface=str((identity.get("origin") or {}).get("channel_type") or self.surface),
            ),
        )
