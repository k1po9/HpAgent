"""Transactional self-service Web registration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

import psycopg
from argon2 import PasswordHasher
from uuid6 import uuid7

from account.identity import normalize_web_subject
from account.invite_service import EntitlementProfile, RegistrationInviteService
from persistence.uow import UnitOfWork

logger = logging.getLogger("HpAgent.Account")

DEFAULT_SELF_SERVICE_ENTITLEMENT = EntitlementProfile(
    model_access_tier="standard",
    daily_token_limit=50_000,
    prompt_visibility="none",
)


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
        self._invites = RegistrationInviteService(database)

    def register(
        self, username: str, password: str, invite_code: str | None = None
    ) -> RegistrationResult:
        subject = normalize_web_subject(username)
        if not subject or len(subject) > 512:
            raise InvalidUsername("invalid username")
        if len(password) < 8 or len(password) > 128:
            raise InvalidPassword("password must contain 8 to 128 characters")

        password_hash = self._hasher.hash(password)
        account_id, binding_id, credential_id = uuid7(), uuid7(), uuid7()
        try:
            with UnitOfWork(self._database) as uow:
                normalized_invite = invite_code.strip() if invite_code else ""
                if normalized_invite:
                    invite_id, profile = self._invites.consume_in_uow(
                        uow, normalized_invite
                    )
                else:
                    invite_id = None
                    profile = DEFAULT_SELF_SERVICE_ENTITLEMENT
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
                uow.execute(
                    "INSERT INTO account_entitlements(account_id,model_access_tier,"
                    "daily_token_limit,prompt_visibility,expires_at,provisioned_by_invite_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (account_id, profile.model_access_tier, profile.daily_token_limit,
                     profile.prompt_visibility, profile.expires_at, invite_id),
                )
                if invite_id is not None:
                    self._invites.record_redemption_in_uow(uow, invite_id)
        except psycopg.errors.UniqueViolation as exc:
            logger.info("web_registration_conflict username=%s", subject)
            raise UsernameAlreadyExists("username already exists") from exc

        logger.info("web_registration_succeeded account_id=%s", account_id)
        return RegistrationResult(account_id, binding_id, subject)
