"""QQ-only surface composition."""
from __future__ import annotations

from dataclasses import dataclass

from application.prompts import PromptLoader
from application.reply import ReplyService
from channels.router import ChannelRouter


@dataclass(frozen=True)
class QQSurfaceServices:
    channel_router: ChannelRouter
    reply_service: ReplyService


def build_qq_surface_services(
    *,
    channel_router: ChannelRouter,
    group_context: object | None,
    prompt_loader: PromptLoader,
) -> QQSurfaceServices:
    """Build presentation adapters only; Agent capabilities are composed elsewhere."""
    reply = ReplyService(
        channel_router=channel_router,
        group_context=group_context,
        prompts=prompt_loader,
    )
    return QQSurfaceServices(
        channel_router=channel_router,
        reply_service=reply,
    )
