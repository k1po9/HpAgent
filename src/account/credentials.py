"""PostgreSQL-backed Web password credentials."""
from __future__ import annotations

import secrets
from typing import Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from account.identity import normalize_web_subject
from persistence.uow import UnitOfWork


class CredentialVerifier(Protocol):
    def verify(self, username: str, password: str) -> str | None: ...


class PresenceAwareCredentialVerifier(CredentialVerifier, Protocol):
    def verify_with_presence(
        self, username: str, password: str
    ) -> tuple[bool, str | None]: ...


class PostgresPasswordCredentialAdapter:
    """Verify an active, verified Web identity against ``web_credentials``."""

    def __init__(self, database: object):
        self._database = database
        self._hasher = PasswordHasher()
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(24))

    def verify(self, username: str, password: str) -> str | None:
        return self.verify_with_presence(username, password)[1]

    def verify_with_presence(
        self, username: str, password: str
    ) -> tuple[bool, str | None]:
        subject = normalize_web_subject(username)
        encoded = self._dummy_hash
        found = False
        usable = False
        if subject:
            with UnitOfWork(self._database) as uow:
                row = uow.execute(
                    "SELECT c.password_hash,b.status,b.verified_at,b.revoked_at,"
                    "a.status AS account_status FROM identity_bindings b "
                    "JOIN accounts a ON a.account_id=b.account_id "
                    "JOIN web_credentials c "
                    "ON c.identity_binding_id=b.identity_binding_id "
                    "WHERE b.provider='web' AND b.normalized_subject_id=%s "
                    "ORDER BY (b.status='active' AND b.verified_at IS NOT NULL "
                    "AND b.revoked_at IS NULL AND a.status='active') DESC,"
                    "b.created_at DESC LIMIT 1",
                    (subject,),
                ).fetchone()
            if row:
                encoded = row["password_hash"]
                found = True
                usable = (
                    row["status"] == "active"
                    and row["verified_at"] is not None
                    and row["revoked_at"] is None
                    and row["account_status"] == "active"
                )
        try:
            verified = self._hasher.verify(encoded, password)
        except (VerifyMismatchError, InvalidHashError):
            verified = False
        return found, subject if found and usable and verified else None


class FallbackCredentialAdapter:
    """Temporary DB-first compatibility bridge for legacy configured hashes."""

    def __init__(
        self,
        primary: PresenceAwareCredentialVerifier,
        fallback: CredentialVerifier,
    ):
        self._primary = primary
        self._fallback = fallback

    def verify(self, username: str, password: str) -> str | None:
        present, verified = self._primary.verify_with_presence(username, password)
        if present:
            return verified
        return self._fallback.verify(username, password)
