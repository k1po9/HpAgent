"""Conversation-owned bindings for the current Chat capability adapters.

Other Run sources supply their own bindings; these checks are not Workflow inputs
or lifecycle prerequisites. This adapter does not create Conversation identities.
"""


class ChatExecutionBindings:
    def session_key(self, request) -> str:
        return request.context.require_chat().session_id

    def transcript_context(self, request) -> dict[str, str]:
        chat = request.context.require_chat()
        return {"conversation_id": chat.conversation_id, "session_id": chat.session_id}

    def validate_loaded(self, request, loaded) -> None:
        chat = request.context.require_chat()
        if (
            loaded.account_id != request.account_id
            or loaded.conversation_id != chat.conversation_id
            or loaded.session_id != chat.session_id
        ):
            raise ValueError("context identity mismatch")
