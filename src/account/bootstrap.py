"""bootstrap_identity —— 显式建立 Web/QQ 身份与同一 PostgreSQL Account 的绑定。

Phase F 身份关联必须提前显式建立（doc §12）。MVP 不支持自动 merge 不同
Account（doc §13 Case E 直接 FAIL）。

幂等语义（doc §14）：重复执行结果仍然只有 1 Account + 1 Web binding +
1 QQ binding。

本模块使用 migration/admin credential（``MIGRATION_DATABASE_URL``），
因为 identity_bindings 属于管理面；Worker 只读。
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from account.identity import QQ_CHANNELS, normalize_qq_subject, normalize_web_subject

logger = logging.getLogger("HpAgent.Account")

@dataclass(frozen=True)
class BootstrapResult:
    """bootstrap 结果。status 取值: created / bound_qq / bound_web / noop。"""

    status: str
    account_id: str


def bootstrap_identity(
    database_url: str,
    *,
    web_subject: str,
    qq_channel: str,
    qq_subject: str,
) -> BootstrapResult:
    """把 web_subject 与 (qq_channel, qq_subject) 关联到同一 Account。

    Cases（doc §13）:
      A. 两者都不存在  → 创建 Account A + Web→A + QQ→A
      B. Web 已属于 A，QQ 不存在 → QQ→A
      C. QQ 已属于 A，Web 不存在 → Web→A
      D. 二者已经属于 A → no-op
      E. Web 属于 A，QQ 属于 B（A≠B）→ 抛 ValueError（不 merge）
    """
    if web_subject.strip() == "":
        raise ValueError("web_subject must not be empty")
    if qq_channel not in QQ_CHANNELS:
        raise ValueError(f"qq_channel must be one of {sorted(QQ_CHANNELS)}")
    if qq_subject.strip() == "":
        raise ValueError("qq_subject must not be empty")

    web_norm = normalize_web_subject(web_subject)
    qq_norm = normalize_qq_subject(qq_channel, qq_subject)
    assert qq_norm is not None

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SET search_path TO hpagent, public")
        with connection.cursor() as cursor:
            web_account = _find_active_account(cursor, "web", web_norm)
            qq_account = _find_active_account(cursor, "qq", qq_norm)

            if web_account and qq_account and web_account != qq_account:
                raise ValueError(
                    "identity conflict: web subject maps to "
                    f"account {web_account} but qq subject maps to {qq_account}; "
                    "Account merge is unsupported in Phase F"
                )

            if web_account and qq_account:
                result = BootstrapResult(status="noop", account_id=str(web_account))
            elif web_account:
                _insert_binding(cursor, web_account, "qq", qq_subject.strip(), qq_norm, qq_channel)
                result = BootstrapResult(status="bound_qq", account_id=str(web_account))
            elif qq_account:
                _insert_binding(cursor, qq_account, "web", web_subject.strip(), web_norm, "web")
                result = BootstrapResult(status="bound_web", account_id=str(qq_account))
            else:
                account_id = uuid.uuid4()
                cursor.execute(
                    "INSERT INTO accounts(account_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (account_id,),
                )
                _insert_binding(cursor, account_id, "web", web_subject.strip(), web_norm, "web")
                _insert_binding(cursor, account_id, "qq", qq_subject.strip(), qq_norm, qq_channel)
                result = BootstrapResult(status="created", account_id=str(account_id))
        connection.commit()

    logger.info(
        "bootstrap_identity status=%s account=%s web=%s qq=%s:%s",
        result.status, result.account_id, web_norm, qq_channel, qq_subject,
    )
    return result


def _find_active_account(cursor, provider: str, normalized: str) -> uuid.UUID | None:
    row = cursor.execute(
        "SELECT account_id FROM identity_bindings "
        "WHERE provider=%s AND normalized_subject_id=%s AND status='active'",
        (provider, normalized),
    ).fetchone()
    return row["account_id"] if row else None


def _insert_binding(
    cursor,
    account_id: uuid.UUID,
    provider: str,
    external_subject_id: str,
    normalized_subject_id: str,
    channel_type: str,
) -> None:
    cursor.execute(
        "INSERT INTO identity_bindings(identity_binding_id, account_id, provider,"
        "external_subject_id, normalized_subject_id, verified_at, metadata) "
        "VALUES (%s,%s,%s,%s,%s,now(),%s::jsonb) ON CONFLICT DO NOTHING",
        (
            uuid.uuid4(),
            account_id,
            provider,
            external_subject_id,
            normalized_subject_id,
            json.dumps({"channel_type": channel_type}),
        ),
    )
