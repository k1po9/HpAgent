from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from uuid6 import uuid7

from account.identity import normalize_web_subject
from persistence.uow import UnitOfWork

from .config import WebApiSettings
from .security import csrf_digest, csrf_token, token_digest


class CredentialAdapter(Protocol):
    def verify(self, username: str, password: str) -> str | None: ...


class ConfiguredPasswordCredentialAdapter:
    """Argon2id verifier whose values are supplied by a secret-backed config."""

    def __init__(self, records: dict[str, str]):
        self._records = {self.normalize(name): value for name, value in records.items()}
        self._hasher = PasswordHasher()
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(24))

    @staticmethod
    def normalize(value: str) -> str:
        return normalize_web_subject(value)

    def verify(self, username: str, password: str) -> str | None:
        subject = self.normalize(username)
        encoded = self._records.get(subject, self._dummy_hash)
        verified = False
        try:
            verified = self._hasher.verify(encoded, password)
        except (VerifyMismatchError, InvalidHashError):
            pass
        return subject if verified and subject in self._records else None


@dataclass(frozen=True)
class AuthContext:
    account_id: UUID
    identity_binding_id: UUID
    web_auth_session_id: UUID
    account_created_at: datetime
    expires_at: datetime
    idle_expires_at: datetime
    csrf_token: str
    raw_session_token: str


class AuthService:
    def __init__(self, database: object, settings: WebApiSettings):
        self.database = database
        self.settings = settings

    def login(self, external_subject: str) -> AuthContext | None:
        now = datetime.now(UTC)
        raw_token = secrets.token_urlsafe(32)
        csrf = csrf_token(raw_token, self.settings.csrf_signing_key)
        with UnitOfWork(self.database) as uow:
            binding = uow.execute(
                "SELECT b.identity_binding_id,b.account_id,a.created_at "
                "FROM identity_bindings b JOIN accounts a ON a.account_id=b.account_id "
                "WHERE b.provider='web' AND b.normalized_subject_id=%s "
                "AND b.status='active' AND b.verified_at IS NOT NULL "
                "AND a.status='active' FOR UPDATE OF b",
                (external_subject,),
            ).fetchone()
            if not binding:
                return None
            uow.execute(
                "UPDATE web_auth_sessions SET revoked_at=COALESCE(revoked_at,now()) "
                "WHERE identity_binding_id=%s AND revoked_at IS NULL",
                (binding["identity_binding_id"],),
            )
            absolute = now + timedelta(seconds=self.settings.session_absolute_seconds)
            idle = min(
                absolute, now + timedelta(seconds=self.settings.session_idle_seconds)
            )
            session_id = uuid7()
            uow.execute(
                "INSERT INTO web_auth_sessions(web_auth_session_id,account_id,"
                "identity_binding_id,token_hash,csrf_secret_hash,expires_at,idle_expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    session_id,
                    binding["account_id"],
                    binding["identity_binding_id"],
                    token_digest(raw_token, self.settings.session_token_pepper),
                    csrf_digest(csrf),
                    absolute,
                    idle,
                ),
            )
            return AuthContext(
                account_id=binding["account_id"],
                identity_binding_id=binding["identity_binding_id"],
                web_auth_session_id=session_id,
                account_created_at=binding["created_at"],
                expires_at=absolute,
                idle_expires_at=idle,
                csrf_token=csrf,
                raw_session_token=raw_token,
            )

    def authenticate(self, raw_token: str) -> AuthContext | None:
        now = datetime.now(UTC)
        csrf = csrf_token(raw_token, self.settings.csrf_signing_key)
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT s.*,a.created_at AS account_created_at FROM web_auth_sessions s "
                "JOIN accounts a ON a.account_id=s.account_id AND a.status='active' "
                "JOIN identity_bindings b ON b.account_id=s.account_id "
                "AND b.identity_binding_id=s.identity_binding_id "
                "AND b.provider='web' AND b.status='active' AND b.verified_at IS NOT NULL "
                "WHERE s.token_hash=%s AND s.revoked_at IS NULL "
                "AND s.expires_at>%s AND s.idle_expires_at>%s FOR UPDATE OF s",
                (token_digest(raw_token, self.settings.session_token_pepper), now, now),
            ).fetchone()
            if not row or not hmac.compare_digest(
                bytes(row["csrf_secret_hash"]), csrf_digest(csrf)
            ):
                return None
            idle = min(
                row["expires_at"],
                now + timedelta(seconds=self.settings.session_idle_seconds),
            )
            uow.execute(
                "UPDATE web_auth_sessions SET last_seen_at=%s,idle_expires_at=%s "
                "WHERE web_auth_session_id=%s",
                (now, idle, row["web_auth_session_id"]),
            )
            return AuthContext(
                account_id=row["account_id"],
                identity_binding_id=row["identity_binding_id"],
                web_auth_session_id=row["web_auth_session_id"],
                account_created_at=row["account_created_at"],
                expires_at=row["expires_at"],
                idle_expires_at=idle,
                csrf_token=csrf,
                raw_session_token=raw_token,
            )

    def revoke(self, context: AuthContext) -> None:
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE web_auth_sessions SET revoked_at=COALESCE(revoked_at,now()) "
                "WHERE account_id=%s AND web_auth_session_id=%s",
                (context.account_id, context.web_auth_session_id),
            )
