"""Chat source adapter for canonical lifecycle input; no lease is acquired here."""

from temporalio.exceptions import ApplicationError

from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AgentRunInput,
    ChatContext,
    RunContext,
    RunSource,
)


class ChatRunInputLoader:
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
        if identity["run_kind"] != "chat" or not all(
            identity.get(key)
            for key in (
                "conversation_id",
                "session_id",
                "trigger_message_id",
            )
        ):
            raise ApplicationError("Chat source context unavailable", non_retryable=True)
        return AgentRunInput(
            schema_version=AGENT_SCHEMA_VERSION,
            run_id=run_id,
            account_id=str(identity["account_id"]),
            strategy=str(identity["agent_strategy"]),
            max_turns=self.max_turns,
            source=RunSource("chat", str(identity["conversation_id"])),
            context=RunContext(
                chat=ChatContext(
                    str(identity["conversation_id"]),
                    str(identity["session_id"]),
                    str(identity["trigger_message_id"]),
                ),
                surface=self.surface,
            ),
        )
