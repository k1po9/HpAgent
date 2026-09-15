"""QQ protocol identity/routing adapter for canonical PostgreSQL commands."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from account.identity import normalize_channel_subject
from common.types import UnifiedMessage
from conversation_domain.surface_commands import (
    IdentityNotBound,
    SurfaceConversationCommands,
    stable_key,
)


class UnboundIdentity(Exception):
    def __init__(self, *, channel_type: str, sender_id: str):
        super().__init__(f"unbound {channel_type} identity: {sender_id}")
        self.channel_type, self.sender_id = channel_type, sender_id


class InvalidQQMessage(ValueError):
    pass


@dataclass(frozen=True)
class QQEnvelope:
    route: dict
    message_key: str
    origin: dict
    provider: str
    subject: str
    triggered: bool

    @property
    def context_key(self) -> str:
        return "qq:" + stable_key(self.route)


def normalize_qq_message(message: UnifiedMessage, channel: str) -> QQEnvelope:
    identity = normalize_channel_subject(channel, message.sender_id)
    metadata = message.metadata
    scope = str(metadata.get("detail_type") or "")
    bot = str(metadata.get("bot_id") or metadata.get("self_id") or "")
    # Never fall back to UnifiedMessage's generated UUID for a provider message.
    external_id = metadata.get("message_id") if channel == "napcat" else metadata.get("msg_id")
    if not identity or channel not in {"napcat", "official_qq"} or not bot or external_id in (None, ""):
        raise InvalidQQMessage("missing QQ identity, bot or external message ID")
    if channel == "napcat" and metadata.get("post_type", "message") != "message":
        raise InvalidQQMessage("not a QQ message event")
    if scope == "private":
        room = message.sender_id
    elif scope == "group":
        room = str(metadata.get("group_id") or metadata.get("group_openid") or "")
    elif scope == "guild":
        room = str(metadata.get("guild_id") or "") + "/" + str(metadata.get("channel_id") or "")
        if not metadata.get("guild_id") or not metadata.get("channel_id"):
            raise InvalidQQMessage("missing guild/channel identity")
    elif scope == "dm":
        room = str(metadata.get("guild_id") or "") + "/" + message.sender_id
        if not metadata.get("guild_id"):
            raise InvalidQQMessage("missing DM guild identity")
    else:
        raise InvalidQQMessage("unsupported QQ event scope")
    if not room:
        raise InvalidQQMessage("missing QQ room identity")
    route = {"channel_type": channel, "bot_id": bot, "scope": scope,
             "room_id": room, "thread_id": str(metadata.get("thread_id") or "")}
    key = "qq:" + stable_key([route, message.sender_id, str(external_id)])
    origin = {**route, "sender_id": message.sender_id, "external_message_id": str(external_id),
              "metadata": {k: metadata[k] for k in (
                  "detail_type", "group_id", "group_openid", "guild_id", "channel_id",
                  "user_openid", "member_openid", "msg_id", "message_id", "sender_name",
                  "iso_timestamp", "timestamp", "self_id", "thread_id",
                  "image_urls", "attachments", "media_urls",
              ) if k in metadata}}
    return QQEnvelope(route, key, origin, *identity,
                      scope not in {"group", "guild"} or bool(metadata.get("is_at_bot")))


class ConversationService:
    def __init__(self, commands: SurfaceConversationCommands):
        self._commands = commands

    async def accept(self, message: UnifiedMessage, channel_type: str, *, envelope: QQEnvelope | None = None,
                     group_context: str = ""):
        source = envelope or normalize_qq_message(message, channel_type)
        if not source.triggered:
            return None
        content = message.content.strip()
        operation, target = "message", None
        if content == "/cancel" or content.startswith("/cancel "):
            operation = "cancel"
            if content != "/cancel":
                try:
                    target = UUID(content.split(maxsplit=1)[1])
                except ValueError as exc:
                    raise InvalidQQMessage("invalid cancellation Run ID") from exc
        origin = {**source.origin, "context_key": source.context_key, "group_context": group_context}
        try:
            return await asyncio.to_thread(
                self._commands.execute, provider=source.provider, subject=source.subject,
                route=source.route, message_key=source.message_key, content=content,
                origin=origin, operation=operation, target_run=target,
            )
        except IdentityNotBound as exc:
            raise UnboundIdentity(channel_type=channel_type, sender_id=message.sender_id) from exc
