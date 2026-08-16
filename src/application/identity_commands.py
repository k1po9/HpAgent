"""Deterministic identity control-plane commands for QQ ingress."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from account.identity import QQ_CHANNELS
from account.identity_binding_service import (
    ChallengeExpired,
    ChallengeNotFound,
    IdentityBindingService,
    IdentityConflict,
)
from common.types import UnifiedMessage

_BIND_QQ = re.compile(r"^\s*绑定\s+(HP-[0-9]{6})\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class IdentityCommandResult:
    handled: bool
    reply: str | None = None


class IdentityCommandService:
    def __init__(self, binding_service: IdentityBindingService):
        self._bindings = binding_service

    async def try_handle(
        self, message: UnifiedMessage, channel_type: str
    ) -> IdentityCommandResult:
        if channel_type not in QQ_CHANNELS:
            return IdentityCommandResult(False)
        match = _BIND_QQ.fullmatch(message.content or "")
        if not match:
            return IdentityCommandResult(False)
        try:
            result = await asyncio.to_thread(
                self._bindings.consume_qq_challenge,
                match.group(1).upper(),
                channel_type,
                message.sender_id,
            )
        except ChallengeExpired:
            return IdentityCommandResult(True, "绑定码已过期，请在网页重新生成。")
        except ChallengeNotFound:
            return IdentityCommandResult(True, "绑定码无效或已被使用。")
        except IdentityConflict:
            return IdentityCommandResult(
                True, "身份冲突，当前账号无法自动合并，请联系管理员处理。"
            )
        suffix = "，已有 QQ 历史账号已保留" if result.consolidated else ""
        return IdentityCommandResult(True, f"QQ 绑定成功{suffix}。")
