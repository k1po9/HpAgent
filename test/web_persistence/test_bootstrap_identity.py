"""
bootstrap_identity —— Phase F 显式身份绑定（doc §12-14 / §62）。

- 幂等：重复执行结果仍只有 1 Account + 1 Web binding + 1 QQ binding。
- Case E（Web→A 且 QQ→B，A≠B）→ ValueError，绝不自动 merge。
- 二者已属于同一 Account → no-op。
- 非法渠道 / 空 subject → ValueError。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from account.bootstrap import bootstrap_identity

pytestmark = pytest.mark.postgres


def _binding(db, account, provider: str, normalized: str) -> None:
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id, account_id, provider,"
        "external_subject_id, normalized_subject_id, verified_at) "
        "VALUES (%s,%s,%s,%s,%s,now())",
        (uuid4(), account, provider, normalized, normalized),
    )


def _counts(db) -> tuple[int, int, int]:
    accounts = db.execute("SELECT count(*) FROM accounts").fetchone()[0]
    web = db.execute(
        "SELECT count(*) FROM identity_bindings WHERE provider='web'"
    ).fetchone()[0]
    qq = db.execute(
        "SELECT count(*) FROM identity_bindings WHERE provider='qq'"
    ).fetchone()[0]
    return accounts, web, qq


def test_idempotent_double_run(db, migration_database_url):
    first = bootstrap_identity(
        migration_database_url,
        web_subject="user@example.com",
        qq_channel="napcat",
        qq_subject="10001",
    )
    # 大小写不同的 web_subject 也必须归一化到同一绑定（casefold）。
    second = bootstrap_identity(
        migration_database_url,
        web_subject="User@Example.com",
        qq_channel="napcat",
        qq_subject="10001",
    )
    assert first.status == "created"
    assert second.status == "noop"
    assert first.account_id == second.account_id
    assert _counts(db) == (1, 1, 1)


def test_case_e_conflict_raises_without_merging(db, migration_database_url):
    a, b = uuid4(), uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s),(%s)", (a, b))
    _binding(db, a, "web", "user@example.com")
    _binding(db, b, "qq", "napcat:10002")
    with pytest.raises(ValueError, match="merge is unsupported"):
        bootstrap_identity(
            migration_database_url,
            web_subject="user@example.com",
            qq_channel="napcat",
            qq_subject="10002",
        )
    # 冲突不产生任何新行（无 merge、无自动建号）。
    assert _counts(db) == (2, 1, 1)


def test_both_bound_to_same_account_is_noop(db, migration_database_url):
    account = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (account,))
    _binding(db, account, "web", "user@example.com")
    _binding(db, account, "qq", "napcat:10003")
    result = bootstrap_identity(
        migration_database_url,
        web_subject="user@example.com",
        qq_channel="napcat",
        qq_subject="10003",
    )
    assert result.status == "noop"
    assert result.account_id == str(account)
    assert _counts(db) == (1, 1, 1)


def test_qq_only_creates_web_binding_on_same_account(db, migration_database_url):
    account = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (account,))
    _binding(db, account, "qq", "napcat:10004")
    result = bootstrap_identity(
        migration_database_url,
        web_subject="user@example.com",
        qq_channel="napcat",
        qq_subject="10004",
    )
    assert result.status == "bound_web"
    assert result.account_id == str(account)
    assert _counts(db) == (1, 1, 1)


def test_invalid_channel_and_empty_subjects_raise(db, migration_database_url):
    with pytest.raises(ValueError, match="qq_channel"):
        bootstrap_identity(
            migration_database_url,
            web_subject="x",
            qq_channel="telegram",
            qq_subject="1",
        )
    with pytest.raises(ValueError, match="web_subject"):
        bootstrap_identity(
            migration_database_url,
            web_subject="   ",
            qq_channel="napcat",
            qq_subject="1",
        )
    with pytest.raises(ValueError, match="qq_subject"):
        bootstrap_identity(
            migration_database_url,
            web_subject="x",
            qq_channel="napcat",
            qq_subject="",
        )
