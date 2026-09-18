from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from account.entitlement_service import EntitlementService, EntitlementState
from account.invite_service import (
    EntitlementProfile,
    InvalidRegistrationInvite,
    RegistrationInviteService,
)
from account.registration_service import RegistrationService

pytestmark = pytest.mark.postgres


def _create_invite(migration_database_url, **kwargs):
    return RegistrationInviteService(migration_database_url).create(
        EntitlementProfile("standard", 12_345, "summary"), **kwargs
    )


def test_valid_invite_provisions_complete_account_atomically(
    db, database_url, migration_database_url
):
    invite = _create_invite(migration_database_url)
    result = RegistrationService(database_url).register("alice", "correct-password", invite.code)

    assert db.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM web_credentials").fetchone()[0] == 1
    entitlement = db.execute(
        "SELECT model_access_tier,daily_token_limit,prompt_visibility,"
        "provisioned_by_invite_id FROM account_entitlements WHERE account_id=%s",
        (result.account_id,),
    ).fetchone()
    assert tuple(entitlement) == ("standard", 12_345, "summary", invite.invite_id)
    assert (
        db.execute(
            "SELECT redemption_count FROM registration_invites WHERE invite_id=%s",
            (invite.invite_id,),
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("condition", ["invalid", "expired", "revoked", "exhausted"])
def test_unavailable_invite_has_stable_failure_and_creates_no_account(
    condition, db, database_url, migration_database_url
):
    if condition == "invalid":
        code = "not-a-real-invite"
    else:
        invite = _create_invite(
            migration_database_url,
            expires_at=(datetime.now(UTC) + timedelta(hours=1)),
        )
        code = invite.code
        if condition == "expired":
            db.execute(
                "UPDATE registration_invites SET expires_at=now()-interval '1 second' "
                "WHERE invite_id=%s",
                (invite.invite_id,),
            )
        elif condition == "revoked":
            db.execute(
                "UPDATE registration_invites SET revoked_at=now() WHERE invite_id=%s",
                (invite.invite_id,),
            )
        else:
            db.execute(
                "UPDATE registration_invites SET redemption_count=max_redemptions "
                "WHERE invite_id=%s",
                (invite.invite_id,),
            )
    with pytest.raises(InvalidRegistrationInvite, match="unavailable"):
        RegistrationService(database_url).register("alice", "correct-password", code)
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0


def test_concurrent_redemption_never_exceeds_limit(db, database_url, migration_database_url):
    invite = _create_invite(migration_database_url, max_redemptions=1)

    def register(username):
        try:
            RegistrationService(database_url).register(username, "correct-password", invite.code)
            return "created"
        except InvalidRegistrationInvite:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(register, ("alice", "bob")))

    assert sorted(outcomes) == ["created", "rejected"]
    assert (
        db.execute(
            "SELECT redemption_count FROM registration_invites WHERE invite_id=%s",
            (invite.invite_id,),
        ).fetchone()[0]
        == 1
    )
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1


def test_entitlement_reader_distinguishes_all_states(db, database_url):
    service = EntitlementService(database_url)
    missing_account = uuid4()
    assert service.get(missing_account).state is EntitlementState.ACCOUNT_UNAVAILABLE
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (missing_account,))
    assert service.get(missing_account).state is EntitlementState.MISSING
    db.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility,expires_at) "
        "VALUES (%s,'standard','none',now()-interval '1 second')",
        (missing_account,),
    )
    assert service.get(missing_account).state is EntitlementState.EXPIRED
    db.execute(
        "UPDATE account_entitlements SET expires_at=NULL WHERE account_id=%s",
        (missing_account,),
    )
    assert service.get(missing_account).state is EntitlementState.VALID
    db.execute("UPDATE accounts SET status='disabled' WHERE account_id=%s", (missing_account,))
    assert service.get(missing_account).state is EntitlementState.ACCOUNT_UNAVAILABLE


def test_migration_contains_deterministic_existing_account_backfill():
    migration = Path("persistence/migrations/037_account_access_governance.sql").read_text()
    assert "SELECT account_id, 'owner', NULL, 'full_safe' FROM accounts" in migration
    assert "ON CONFLICT(account_id) DO NOTHING" in migration
