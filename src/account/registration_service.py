"""Transactional self-service Web registration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

import psycopg
from argon2 import PasswordHasher
from uuid6 import uuid7

from account.identity import normalize_web_subject
from persistence.uow import UnitOfWork

logger = logging.getLogger("HpAgent.Account")


class RegistrationError(Exception):
    pass


class UsernameAlreadyExists(RegistrationError):
    pass


class InvalidUsername(RegistrationError):
    pass


class InvalidPassword(RegistrationError):
    pass


@dataclass(frozen=True)
class RegistrationResult:
    account_id: UUID
    identity_binding_id: UUID
    normalized_subject: str


class RegistrationService:
    def __init__(self, database: object):
        self._database = database
        self._hasher = PasswordHasher()

    def register(self, username: str, password: str) -> RegistrationResult:
        subject = normalize_web_subject(username)
        if not subject or len(subject) > 512:
            raise InvalidUsername("invalid username")
        if len(password) < 8 or len(password) > 128:
            raise InvalidPassword("password must contain 8 to 128 characters")

        password_hash = self._hasher.hash(password)
        account_id, binding_id, credential_id = uuid7(), uuid7(), uuid7()
        try:
            with UnitOfWork(self._database) as uow:
                uow.execute("INSERT INTO accounts(account_id) VALUES (%s)", (account_id,))
                uow.execute(
                    "INSERT INTO identity_bindings(identity_binding_id,account_id,"
                    "provider,external_subject_id,normalized_subject_id,verified_at,metadata) "
                    "VALUES (%s,%s,'web',%s,%s,now(),'{\"channel_type\":\"web\"}'::jsonb)",
                    (binding_id, account_id, username.strip(), subject),
                )
                uow.execute(
                    "INSERT INTO web_credentials(web_credential_id,identity_binding_id,"
                    "password_hash) VALUES (%s,%s,%s)",
                    (credential_id, binding_id, password_hash),
                )
        except psycopg.errors.UniqueViolation as exc:
            logger.info("web_registration_conflict username=%s", subject)
            raise UsernameAlreadyExists("username already exists") from exc

        logger.info("web_registration_succeeded account_id=%s", account_id)
        return RegistrationResult(account_id, binding_id, subject)
