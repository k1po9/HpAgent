from common.types import ChannelType, UnifiedMessage


def qq_message(mid="1", content="hello", *, sender="123", scope="private", bot="bot", room="42", thread="", trigger=True):
    return UnifiedMessage(
        sender_id=sender, channel_type=ChannelType.NAPCAT, content=content,
        metadata={"post_type": "message", "message_id": mid, "self_id": bot,
                  "detail_type": scope, "group_id": room if scope == "group" else "",
                  "thread_id": thread, "is_at_bot": trigger},
    )
