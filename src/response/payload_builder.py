from src.core.types import ReplyPayload


def build_reply_payload(model_response: str, is_error: bool = False) -> ReplyPayload:
    return ReplyPayload(text=model_response, is_error=is_error)
