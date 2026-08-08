"""PostgresAccountService —— 基于 PostgreSQL identity_bindings 的账号解析。

Phase F 统一身份：QQ 与 Web 都解析到 ``accounts.account_id``，Hindsight
bank 因而天然统一为 ``hpagent-u-{account_id}``。

与旧 ``AccountService``（accounts.json）的最小兼容接口：
  - ``resolve(channel_type, channel_user_id) -> str | None``
  - ``list_all_ids() -> list[str]``

行为约束（Phase F 设计决策）:
  - **只读**：本服务只 SELECT ``identity_bindings`` / ``accounts``，
    绝不自动创建 Account 或 IdentityBinding。
  - **不降级**：解析不到活跃绑定返回 ``None``，绝不回落到 accounts.json。
  - **失败即失败**：SELECT 本身失败（DB 挂/连接/权限）抛
    ``IdentityResolutionUnavailable``，绝不伪装成"未绑定"；ingress 回复
    "服务暂时不可用"而不是"账号尚未绑定"。
  - 身份绑定是管理面（bootstrap 脚本），Worker 只负责读取。
"""
from __future__ import annotations

import asyncio
import logging

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger("HpAgent.Account")

# provider='qq' 同时承载 NapCat QQ 号与 Official QQ OpenID，二者不是同一个
# ID namespace，因此 normalized_subject_id 必须带 channel 前缀。
_QQ_CHANNELS = frozenset({"napcat", "official_qq"})


class IdentityResolutionUnavailable(Exception):
    """PostgreSQL 身份解析不可用（DB 故障 / 连接失败 / 权限错误）。

    与 ``UnboundIdentity``（业务上确实没有绑定）严格区分：基础设施错误绝不
    伪装成"账号尚未绑定"。ingress 捕获后回复"服务暂时不可用"并记录结构化
    ``qq_identity_resolution_unavailable`` 告警，而不是走 UnboundIdentity。
    """

    def __init__(self, provider: str, normalized_subject: str):
        super().__init__(
            f"identity resolution unavailable for {provider}:{normalized_subject}"
        )
        self.provider = provider
        self.normalized_subject = normalized_subject


class PostgresAccountService:
    """渠道身份 → PostgreSQL account_id 的只读解析服务。"""

    def __init__(self, database_url: str):
        self._database_url = database_url

    @staticmethod
    def normalize_subject(channel_type: str, channel_user_id: str) -> tuple[str, str] | None:
        """返回 ``(provider, normalized_subject_id)``；未知渠道返回 None。

        QQ: normalized = "{channel}:{subject}"（napcat / official_qq）
        Web: normalized = subject.strip().casefold()
             必须与 ``ConfiguredPasswordCredentialAdapter.normalize`` 完全一致。
        """
        subject = (channel_user_id or "").strip()
        if not subject:
            return None
        if channel_type in _QQ_CHANNELS:
            return "qq", f"{channel_type}:{subject}"
        if channel_type == "web":
            return "web", subject.casefold()
        return None

    async def resolve(self, channel_type: str, channel_user_id: str) -> str | None:
        """解析渠道身份到 account_id；无活跃绑定返回 None（不自动创建）。"""
        normalized = self.normalize_subject(channel_type, channel_user_id)
        if normalized is None:
            return None
        provider, normalized_subject = normalized
        return await asyncio.to_thread(
            self._resolve_sync, provider, normalized_subject
        )

    def _resolve_sync(self, provider: str, normalized_subject: str) -> str | None:
        try:
            with psycopg.connect(
                self._database_url, row_factory=dict_row
            ) as connection:
                connection.execute("SET search_path TO hpagent, public")
                row = connection.execute(
                    "SELECT b.account_id "
                    "FROM identity_bindings b "
                    "JOIN accounts a ON a.account_id = b.account_id "
                    "WHERE b.provider=%s AND b.normalized_subject_id=%s "
                    "AND b.status='active' AND b.verified_at IS NOT NULL "
                    "AND b.revoked_at IS NULL AND a.status='active'",
                    (provider, normalized_subject),
                ).fetchone()
                return str(row["account_id"]) if row else None
        except Exception as e:
            # 基础设施故障 ≠ 业务未绑定。绝不让 DB 挂了假装成"账号尚未绑定"
            # （doc §17）：抛 IdentityResolutionUnavailable，由 ingress 发送
            # 服务不可用回复。也绝不创建账号。
            logger.warning(
                "qq_identity_resolution_unavailable provider=%s normalized=%s error=%s",
                provider, normalized_subject, e,
            )
            raise IdentityResolutionUnavailable(
                provider, normalized_subject
            ) from e

    def list_all_ids(self) -> list[str]:
        """返回所有活跃 account_id（供 Reflect Schedule 遍历）。

        同步方法：``_setup_reflect_schedule`` 在没有 await 的上下文调用它。
        """
        try:
            with psycopg.connect(
                self._database_url, row_factory=dict_row
            ) as connection:
                connection.execute("SET search_path TO hpagent, public")
                rows = connection.execute(
                    "SELECT account_id FROM accounts WHERE status='active'"
                ).fetchall()
                return [str(row["account_id"]) for row in rows]
        except Exception as e:
            logger.warning("Account list_all_ids failed: %s", e)
            return []
