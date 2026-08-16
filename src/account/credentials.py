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


class PostgresPasswordCredentialAdapter:
    """Verify an active, verified Web identity against ``web_credentials``."""

    def __init__(self, database: object):
        self._database = database
        self._hasher = PasswordHasher()
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(24))

    def verify(self, username: str, password: str) -> str | None:
        subject = normalize_web_subject(username)
        encoded = self._dummy_hash
        found = False
        if subject:
            with UnitOfWork(self._database) as uow:
                row = uow.execute(
                    "SELECT c.password_hash FROM identity_bindings b "
                    "JOIN accounts a ON a.account_id=b.account_id "
                    "JOIN web_credentials c "
                    "ON c.identity_binding_id=b.identity_binding_id "
                    "WHERE b.provider='web' AND b.normalized_subject_id=%s "
                    "AND b.status='active' AND b.verified_at IS NOT NULL "
                    "AND b.revoked_at IS NULL AND a.status='active'",
                    (subject,),
                ).fetchone()
            if row:
                encoded = row["password_hash"]
                found = True
        try:
            verified = self._hasher.verify(encoded, password)
        except (VerifyMismatchError, InvalidHashError):
            verified = False
        return subject if found and verified else None


class FallbackCredentialAdapter:
    """Temporary DB-first compatibility bridge for legacy configured hashes."""

    def __init__(self, primary: CredentialVerifier, fallback: CredentialVerifier):
        self._primary = primary
        self._fallback = fallback

    def verify(self, username: str, password: str) -> str | None:
        return self._primary.verify(username, password) or self._fallback.verify(
            username, password
        )
