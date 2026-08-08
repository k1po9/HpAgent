"""
PostgresAccountService —— Phase F 统一身份解析（doc §17 / §62）。

① 同一 QQ 绑定 → 返回其 account_id
② Web 与 QQ 绑定指向同一 Account → 同一 account_id
③ 不同 QQ 发送者 → 不同 account_id
- 未知 subject / 未知渠道 / revoked 绑定 → None（绝不自动创建）
- list_all_ids 只返回活跃 Account
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from account.postgres_account_service import PostgresAccountService

pytestmark = pytest.mark.postgres


def _account(db) -> uuid4:
    value = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (value,))
    return value


def _binding(db, account, provider: str, normalized: str, external: str | None = None) -> None:
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id, account_id, provider,"
        "external_subject_id, normalized_subject_id, verified_at) "
        "VALUES (%s,%s,%s,%s,%s,now())",
        (uuid4(), account, provider, external or normalized, normalized),
    )


@pytest.mark.asyncio
async def test_same_qq_binding_resolves_to_its_account(db, worker_database_url):
    account = _account(db)
    _binding(db, account, "qq", "napcat:10001", external="10001")
    service = PostgresAccountService(worker_database_url)
    assert await service.resolve("napcat", "10001") == str(account)


@pytest.mark.asyncio
async def test_web_and_qq_bindings_point_to_same_account(db, worker_database_url):
    account = _account(db)
    _binding(db, account, "web", "user@example.com", external="user@example.com")
    _binding(db, account, "qq", "napcat:10002", external="10002")
    service = PostgresAccountService(worker_database_url)
    # Web 归一化必须与 ConfiguredPasswordCredentialAdapter.normalize 一致：
    # subject.strip().casefold() → "user@example.com" 命中。
    web = await service.resolve("web", "User@Example.com")
    qq = await service.resolve("napcat", "10002")
    assert web == qq == str(account)


@pytest.mark.asyncio
async def test_different_qq_subjects_map_to_different_accounts(db, worker_database_url):
    a, b = _account(db), _account(db)
    _binding(db, a, "qq", "napcat:111", external="111")
    _binding(db, b, "qq", "napcat:222", external="222")
    service = PostgresAccountService(worker_database_url)
    assert await service.resolve("napcat", "111") == str(a)
    assert await service.resolve("napcat", "222") == str(b)
    # 未知 subject → None。
    assert await service.resolve("napcat", "333") is None


@pytest.mark.asyncio
async def test_unknown_channel_never_resolves(db, worker_database_url):
    service = PostgresAccountService(worker_database_url)
    assert await service.resolve("console", "someone") is None
    assert await service.resolve("", "someone") is None


@pytest.mark.asyncio
async def test_revoked_binding_is_not_resolved(db, worker_database_url):
    account = _account(db)
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id, account_id, provider,"
        "external_subject_id, normalized_subject_id, status, verified_at, revoked_at) "
        "VALUES (%s,%s,'qq','4000','napcat:4000','revoked',now(),now())",
        (uuid4(), account),
    )
    service = PostgresAccountService(worker_database_url)
    assert await service.resolve("napcat", "4000") is None


def test_list_all_ids_returns_active_accounts(db, worker_database_url):
    a, b = _account(db), _account(db)
    service = PostgresAccountService(worker_database_url)
    ids = service.list_all_ids()
    assert {str(a), str(b)} <= set(ids)
