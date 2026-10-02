"""Chat context validation without a Session or Git prerequisite."""


class ChatExecutionBindings:
    def resource_key(self, request) -> str:
        return f"{request.execution_id}:{request.lease_token}"

    def transcript_context(self, request) -> dict[str, str | None]:
        chat = request.context.require_chat()
        return {"conversation_id": chat.conversation_id, "session_id": None,
                "execution_id": request.execution_id}

    def validate_loaded(self, request, loaded) -> None:
        chat = request.context.require_chat()
        if (loaded.account_id != request.account_id or
                loaded.conversation_id != chat.conversation_id or
                loaded.execution_id != request.execution_id):
            raise ValueError("context identity mismatch")
