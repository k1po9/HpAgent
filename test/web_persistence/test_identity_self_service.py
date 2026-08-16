from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from argon2 import PasswordHasher

from account.credentials import (
    FallbackCredentialAdapter,
    PostgresPasswordCredentialAdapter,
)
from account.identity_binding_service import (
    ChallengeExpired,
    ChallengeNotFound,
    IdentityBindingService,
    IdentityConflict,
)
from account.registration_service import (
    InvalidPassword,
    RegistrationService,
    UsernameAlreadyExists,
)
from web_api.auth import ConfiguredPasswordCredentialAdapter

pytestmark = pytest.mark.postgres


def _qq_account(db, channel="napcat", subject="123456"):
    account_id, binding_id = uuid4(), uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (account_id,))
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,"
        "external_subject_id,normalized_subject_id,verified_at,metadata) "
        "VALUES (%s,%s,'qq',%s,%s,now(),"
        "jsonb_build_object('channel_type',%s::text))",
        (binding_id, account_id, subject, f"{channel}:{subject}", channel),
    )
    return account_id


def test_registration_and_postgres_login_are_atomic_and_normalized(db, database_url):
    registration = RegistrationService(database_url)
    result = registration.register(" Alice ", "correct-password")
    adapter = PostgresPasswordCredentialAdapter(database_url)

    assert adapter.verify("ALICE", "correct-password") == "alice"
    assert adapter.verify("alice", "wrong-password") is None
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM web_credentials").fetchone()[0] == 1
    with pytest.raises(UsernameAlreadyExists):
        registration.register("aLiCe", "another-password")
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1
    db.execute(
        "UPDATE identity_bindings SET status='revoked',revoked_at=now() "
        "WHERE identity_binding_id=%s",
        (result.identity_binding_id,),
    )
    assert adapter.verify("alice", "correct-password") is None


def test_registration_password_policy_leaves_no_partial_account(db, database_url):
    with pytest.raises(InvalidPassword):
        RegistrationService(database_url).register("alice", "short")
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0


def test_database_credential_is_authoritative_over_legacy_fallback(db, database_url):
    registration = RegistrationService(database_url)
    registration.register("alice", "database-password")
    bob_account, bob_binding = uuid4(), uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (bob_account,))
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,"
        "external_subject_id,normalized_subject_id,verified_at) "
        "VALUES (%s,%s,'web','bob','bob',now())",
        (bob_binding, bob_account),
    )
    hasher = PasswordHasher()
    adapter = FallbackCredentialAdapter(
        PostgresPasswordCredentialAdapter(database_url),
        ConfiguredPasswordCredentialAdapter(
            {
                "alice": hasher.hash("legacy-password"),
                "bob": hasher.hash("legacy-password"),
            }
        ),
    )

    assert adapter.verify("alice", "database-password") == "alice"
    assert adapter.verify("alice", "legacy-password") is None
    assert adapter.verify("bob", "legacy-password") == "bob"


def test_binding_unbound_qq_and_single_use(db, database_url, worker_database_url):
    web = RegistrationService(database_url).register("alice", "correct-password")
    api_service = IdentityBindingService(database_url, b"test-pepper", 300)
    service = IdentityBindingService(worker_database_url, b"test-pepper", 300)
    challenge = api_service.create_qq_challenge(web.account_id)

    result = service.consume_qq_challenge(challenge.code, "napcat", "123456")
    replay = service.consume_qq_challenge(challenge.code, "napcat", "123456")

    assert result.account_id == web.account_id
    assert replay.status == "completed"
    assert db.execute(
        "SELECT account_id FROM identity_bindings WHERE normalized_subject_id='napcat:123456'"
    ).fetchone()[0] == web.account_id


def test_concurrent_challenge_consume_is_idempotent(
    db, database_url, worker_database_url
):
    web = RegistrationService(database_url).register("alice", "correct-password")
    challenge = IdentityBindingService(
        database_url, b"test-pepper", 300
    ).create_qq_challenge(web.account_id)

    def consume():
        return IdentityBindingService(
            worker_database_url, b"test-pepper", 300
        ).consume_qq_challenge(challenge.code, "napcat", "123456")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))

    assert [result.status for result in results] == ["completed", "completed"]
    assert db.execute(
        "SELECT count(*) FROM identity_bindings "
        "WHERE provider='qq' AND normalized_subject_id='napcat:123456'"
    ).fetchone()[0] == 1


def test_challenge_rotation_expiry_and_invalid_code(
    db, database_url, worker_database_url
):
    web = RegistrationService(database_url).register("alice", "correct-password")
    api_service = IdentityBindingService(database_url, b"test-pepper", 300)
    worker_service = IdentityBindingService(worker_database_url, b"test-pepper", 300)
    old = api_service.create_qq_challenge(web.account_id)
    current = api_service.create_qq_challenge(web.account_id)
    assert api_service.get_qq_challenge(web.account_id, old.challenge_id).status == "cancelled"
    db.execute(
        "UPDATE identity_binding_challenges SET expires_at=created_at + interval '1 second' "
        "WHERE challenge_id=%s",
        (current.challenge_id,),
    )
    db.execute(
        "UPDATE identity_binding_challenges SET created_at=created_at - interval '1 hour',"
        "expires_at=expires_at - interval '1 hour' WHERE challenge_id=%s",
        (current.challenge_id,),
    )
    with pytest.raises(ChallengeExpired):
        worker_service.consume_qq_challenge(current.code, "napcat", "123456")
    persisted = db.execute(
        "SELECT status,cancelled_at FROM identity_binding_challenges "
        "WHERE challenge_id=%s",
        (current.challenge_id,),
    ).fetchone()
    assert persisted[0] == "cancelled"
    assert persisted[1] is not None
    with pytest.raises(ChallengeNotFound):
        worker_service.consume_qq_challenge("HP-000001", "napcat", "123456")


def test_empty_web_account_consolidates_into_existing_qq_account(
    db, database_url, worker_database_url
):
    qq_account = _qq_account(db)
    web = RegistrationService(database_url).register("alice", "correct-password")
    api_service = IdentityBindingService(database_url, b"test-pepper", 300)
    service = IdentityBindingService(worker_database_url, b"test-pepper", 300)
    challenge = api_service.create_qq_challenge(web.account_id)

    result = service.consume_qq_challenge(challenge.code, "napcat", "123456")

    assert result.consolidated is True
    assert result.account_id == qq_account
    assert db.execute(
        "SELECT account_id FROM identity_bindings WHERE provider='web'"
    ).fetchone()[0] == qq_account
    assert db.execute(
        "SELECT status FROM accounts WHERE account_id=%s", (web.account_id,)
    ).fetchone()[0] == "disabled"
    assert api_service.get_qq_challenge(qq_account, challenge.challenge_id).status == "completed"


def test_web_account_with_business_data_is_not_auto_consolidated(
    db, database_url, worker_database_url
):
    qq_account = _qq_account(db)
    web = RegistrationService(database_url).register("alice", "correct-password")
    db.execute(
        "INSERT INTO conversations(conversation_id,account_id,title) VALUES (%s,%s,'used')",
        (uuid4(), web.account_id),
    )
    api_service = IdentityBindingService(database_url, b"test-pepper", 300)
    service = IdentityBindingService(worker_database_url, b"test-pepper", 300)
    challenge = api_service.create_qq_challenge(web.account_id)

    with pytest.raises(IdentityConflict):
        service.consume_qq_challenge(challenge.code, "napcat", "123456")

    assert db.execute(
        "SELECT account_id FROM identity_bindings WHERE provider='web'"
    ).fetchone()[0] == web.account_id
    assert db.execute(
        "SELECT status FROM accounts WHERE account_id=%s", (qq_account,)
    ).fetchone()[0] == "active"
