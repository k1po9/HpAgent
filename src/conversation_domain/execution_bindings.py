"""Conversation-owned bindings for the current Chat capability adapters.

Other Run sources supply their own bindings; these checks are not Workflow inputs
or lifecycle prerequisites. This adapter does not create Conversation identities.
"""

from typing import Protocol


class _ChatIdentity(Protocol):
    conversation_id: str
    session_id: str


class _RunContext(Protocol):
    def require_chat(self) -> _ChatIdentity: ...


class _ChatRequest(Protocol):
    account_id: str
    context: _RunContext


class _LoadedChatIdentity(Protocol):
    account_id: str
    conversation_id: str
    session_id: str


class ChatExecutionBindings:
    def session_key(self, request: _ChatRequest) -> str:
        return request.context.require_chat().session_id

    def transcript_context(self, request: _ChatRequest) -> dict[str, str | None]:
        chat = request.context.require_chat()
        return {"conversation_id": chat.conversation_id, "session_id": chat.session_id}

    def validate_loaded(
        self, request: _ChatRequest, loaded: _LoadedChatIdentity
    ) -> None:
        chat = request.context.require_chat()
        if (
            loaded.account_id != request.account_id
            or loaded.conversation_id != chat.conversation_id
            or loaded.session_id != chat.session_id
        ):
            raise ValueError("context identity mismatch")
