"""QQ ownership challenges and narrowly-scoped identity consolidation."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from uuid6 import uuid7

from account.identity import normalize_qq_subject
from persistence.uow import UnitOfWork, retryable_transaction

logger = logging.getLogger("HpAgent.Account")


class IdentityBindingError(Exception):
    pass


class ChallengeNotFound(IdentityBindingError):
    pass


class ChallengeExpired(IdentityBindingError):
    pass


class IdentityConflict(IdentityBindingError):
    pass


@dataclass(frozen=True)
class CreatedChallenge:
    challenge_id: UUID
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class ChallengeStatus:
    challenge_id: UUID
    status: str
    expires_at: datetime
    verified_subject_id: str | None = None


@dataclass(frozen=True)
class ConsumeResult:
    challenge_id: UUID
    account_id: UUID
    status: str
    consolidated: bool = False


class IdentityBindingService:
    def __init__(self, database: object, code_pepper: bytes, ttl_seconds: int = 300):
        if not code_pepper:
            raise ValueError("QQ binding code pepper must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("QQ binding challenge TTL must be positive")
        self._database = database
        self._pepper = code_pepper
        self._ttl_seconds = ttl_seconds

    def _digest(self, code: str) -> bytes:
        return hmac.new(
            self._pepper, code.strip().upper().encode("ascii"), hashlib.sha256
        ).digest()

    def create_qq_challenge(self, account_id: UUID) -> CreatedChallenge:
        for _ in range(3):
            code = f"HP-{secrets.randbelow(1_000_000):06d}"
            challenge_id = uuid7()
            expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl_seconds)
            try:
                with UnitOfWork(self._database) as uow:
                    uow.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,48284366))",
                        (str(account_id),),
                    )
                    account = uow.execute(
                        "SELECT account_id FROM accounts WHERE account_id=%s "
                        "AND status='active'",
                        (account_id,),
                    ).fetchone()
                    if not account:
                        raise IdentityConflict("account is not active")
                    uow.execute(
                        "UPDATE identity_binding_challenges SET status='cancelled',"
                        "cancelled_at=now() WHERE account_id=%s AND provider='qq' "
                        "AND status='pending'",
                        (account_id,),
                    )
                    uow.execute(
                        "INSERT INTO identity_binding_challenges(challenge_id,account_id,"
                        "provider,challenge_code_hash,expires_at) "
                        "VALUES (%s,%s,'qq',%s,%s)",
                        (challenge_id, account_id, self._digest(code), expires_at),
                    )
                logger.info(
                    "qq_binding_challenge_created account_id=%s challenge_id=%s",
                    account_id,
                    challenge_id,
                )
                return CreatedChallenge(challenge_id, code, expires_at)
            except psycopg.errors.UniqueViolation:
                continue
        raise RuntimeError("could not allocate a unique QQ binding code")

    def get_qq_challenge(
        self, account_id: UUID, challenge_id: UUID
    ) -> ChallengeStatus:
        with UnitOfWork(self._database) as uow:
            row = uow.execute(
                "SELECT challenge_id,status,expires_at,verified_subject_id "
                "FROM identity_binding_challenges WHERE challenge_id=%s "
                "AND account_id=%s AND provider='qq'",
                (challenge_id, account_id),
            ).fetchone()
        if not row:
            raise ChallengeNotFound("challenge not found")
        status = row["status"]
        if status == "pending" and row["expires_at"] <= datetime.now(UTC):
            status = "expired"
        return ChallengeStatus(
            row["challenge_id"], status, row["expires_at"], row["verified_subject_id"]
        )

    def get_identity_summary(self, account_id: UUID) -> dict[str, object]:
        with UnitOfWork(self._database) as uow:
            rows = uow.execute(
                "SELECT provider,external_subject_id,metadata FROM identity_bindings "
                "WHERE account_id=%s AND status='active' AND verified_at IS NOT NULL "
                "ORDER BY created_at",
                (account_id,),
            ).fetchall()
        web = next((row for row in rows if row["provider"] == "web"), None)
        qq = next((row for row in rows if row["provider"] == "qq"), None)
        return {
            "web": {"username": web["external_subject_id"]} if web else None,
            "qq": (
                {
                    "bound": True,
                    "channel_type": qq["metadata"].get("channel_type"),
                    "display_subject": mask_qq_subject(qq["external_subject_id"]),
                }
                if qq
                else {"bound": False}
            ),
        }

    @retryable_transaction
    def consume_qq_challenge(
        self, code: str, channel_type: str, sender_id: str
    ) -> ConsumeResult:
        normalized = normalize_qq_subject(channel_type, sender_id)
        if normalized is None:
            raise ChallengeNotFound("unsupported QQ identity")
        now = datetime.now(UTC)
        with UnitOfWork(self._database) as uow:
            challenge = uow.execute(
                "SELECT * FROM identity_binding_challenges "
                "WHERE provider='qq' AND challenge_code_hash=%s FOR UPDATE",
                (self._digest(code),),
            ).fetchone()
            if not challenge:
                raise ChallengeNotFound("invalid challenge code")
            if challenge["status"] == "completed":
                if challenge["verified_normalized_subject_id"] == normalized:
                    bound = uow.execute(
                        "SELECT account_id FROM identity_bindings WHERE provider='qq' "
                        "AND normalized_subject_id=%s AND status='active'",
                        (normalized,),
                    ).fetchone()
                    if bound:
                        return ConsumeResult(
                            challenge["challenge_id"], bound["account_id"], "completed"
                        )
                raise ChallengeNotFound("challenge already consumed")
            if challenge["status"] != "pending":
                raise ChallengeNotFound("challenge is not pending")
            if challenge["expires_at"] <= now:
                uow.execute(
                    "UPDATE identity_binding_challenges SET status='cancelled',"
                    "cancelled_at=%s WHERE challenge_id=%s",
                    (now, challenge["challenge_id"]),
                )
                raise ChallengeExpired("challenge expired")

            source_account = challenge["account_id"]
            source = uow.execute(
                "SELECT account_id,status FROM accounts WHERE account_id=%s FOR UPDATE",
                (source_account,),
            ).fetchone()
            if not source or source["status"] != "active":
                raise IdentityConflict("challenge account is not active")
            qq_binding = uow.execute(
                "SELECT identity_binding_id,account_id FROM identity_bindings "
                "WHERE provider='qq' AND normalized_subject_id=%s "
                "AND status='active' FOR UPDATE",
                (normalized,),
            ).fetchone()

            consolidated = False
            final_account = source_account
            if qq_binding is None:
                other_qq = uow.execute(
                    "SELECT 1 FROM identity_bindings WHERE account_id=%s "
                    "AND provider='qq' AND status='active' FOR UPDATE",
                    (source_account,),
                ).fetchone()
                if other_qq:
                    raise IdentityConflict("account already has a QQ identity")
                uow.execute(
                    "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,"
                    "external_subject_id,normalized_subject_id,verified_at,metadata) "
                    "VALUES (%s,%s,'qq',%s,%s,%s,"
                    "jsonb_build_object('channel_type',%s::text))",
                    (uuid7(), source_account, sender_id.strip(), normalized, now, channel_type),
                )
            elif qq_binding["account_id"] != source_account:
                final_account = qq_binding["account_id"]
                target = uow.execute(
                    "SELECT account_id,status FROM accounts WHERE account_id=%s FOR UPDATE",
                    (final_account,),
                ).fetchone()
                if not target or target["status"] != "active":
                    raise IdentityConflict("existing QQ account is not active")
                self._consolidate_empty_web_account(
                    uow, source_account, final_account, challenge["challenge_id"]
                )
                consolidated = True

            uow.execute(
                "UPDATE identity_binding_challenges SET status='completed',"
                "verified_subject_id=%s,verified_normalized_subject_id=%s,"
                "verified_at=%s,consumed_at=%s WHERE challenge_id=%s",
                (sender_id.strip(), normalized, now, now, challenge["challenge_id"]),
            )
            logger.info(
                "qq_binding_challenge_completed account_id=%s challenge_id=%s channel_type=%s",
                final_account,
                challenge["challenge_id"],
                channel_type,
            )
            return ConsumeResult(
                challenge["challenge_id"], final_account, "completed", consolidated
            )

    def _consolidate_empty_web_account(
        self,
        uow: UnitOfWork,
        source_account: UUID,
        target_account: UUID,
        current_challenge_id: UUID,
    ) -> None:
        identities = uow.execute(
            "SELECT identity_binding_id,provider,status FROM identity_bindings "
            "WHERE account_id=%s FOR UPDATE",
            (source_account,),
        ).fetchall()
        active_web = [
            row for row in identities if row["provider"] == "web" and row["status"] == "active"
        ]
        if len(identities) != 1 or len(active_web) != 1:
            raise IdentityConflict("Web account is not safe to consolidate")
        if uow.execute(
            "SELECT 1 FROM identity_bindings WHERE account_id=%s "
            "AND provider='web' AND status='active'",
            (target_account,),
        ).fetchone():
            raise IdentityConflict("existing QQ account already has a Web identity")
        binding_id = active_web[0]["identity_binding_id"]
        if not uow.execute(
            "SELECT 1 FROM web_credentials WHERE identity_binding_id=%s",
            (binding_id,),
        ).fetchone():
            raise IdentityConflict("Web account has no credential")
        for table in (
            "conversations",
            "idempotency_commands",
            "outbox_events",
            "artifacts",
            "artifact_outbox_events",
        ):
            if uow.execute(
                f"SELECT 1 FROM {table} WHERE account_id=%s LIMIT 1", (source_account,)
            ).fetchone():
                logger.info(
                    "identity_consolidation_rejected account_id=%s table=%s",
                    source_account,
                    table,
                )
                raise IdentityConflict("Web account contains user data")

        logger.info(
            "identity_consolidation_started source_account_id=%s target_account_id=%s",
            source_account,
            target_account,
        )
        uow.execute("SET CONSTRAINTS fk_web_auth_sessions__identity_bindings DEFERRED")
        uow.execute(
            "UPDATE web_auth_sessions SET account_id=%s WHERE account_id=%s "
            "AND identity_binding_id=%s",
            (target_account, source_account, binding_id),
        )
        uow.execute(
            "UPDATE identity_bindings SET account_id=%s,version=version+1,updated_at=now() "
            "WHERE account_id=%s AND identity_binding_id=%s",
            (target_account, source_account, binding_id),
        )
        uow.execute(
            "UPDATE identity_binding_challenges SET status='cancelled',cancelled_at=now() "
            "WHERE account_id=%s AND status='pending' AND challenge_id<>%s",
            (source_account, current_challenge_id),
        )
        uow.execute(
            "UPDATE identity_binding_challenges SET account_id=%s "
            "WHERE challenge_id=%s",
            (target_account, current_challenge_id),
        )
        uow.execute(
            "UPDATE accounts SET status='disabled',version=version+1,updated_at=now() "
            "WHERE account_id=%s",
            (source_account,),
        )
        logger.info(
            "identity_consolidation_completed source_account_id=%s target_account_id=%s",
            source_account,
            target_account,
        )


def mask_qq_subject(subject: str) -> str:
    value = subject.strip()
    if len(value) <= 4:
        return "*" * len(value)
    if len(value) <= 7:
        return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"
    return f"{value[:3]}{'*' * (len(value) - 6)}{value[-3:]}"
