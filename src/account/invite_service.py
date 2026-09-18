"""Provisioning-only registration invites."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from uuid6 import uuid7

from persistence.uow import UnitOfWork


class InvalidRegistrationInvite(Exception):
    """Stable, deliberately non-specific registration failure."""


@dataclass(frozen=True)
class EntitlementProfile:
    model_access_tier: str
    daily_token_limit: int | None
    prompt_visibility: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class CreatedInvite:
    invite_id: UUID
    code: str


def hash_invite_code(code: str) -> bytes:
    return hashlib.sha256(code.strip().encode("utf-8")).digest()


def parse_entitlement_profile(value: dict[str, Any]) -> EntitlementProfile:
    tier = value.get("model_access_tier")
    limit = value.get("daily_token_limit")
    visibility = value.get("prompt_visibility")
    raw_expiry = value.get("expires_at")
    if not isinstance(tier, str) or not tier.strip():
        raise ValueError("model_access_tier must not be empty")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise ValueError("daily_token_limit must be a positive integer or null")
    if visibility not in {"none", "summary", "full_safe"}:
        raise ValueError("prompt_visibility must be none, summary, or full_safe")
    expiry = None
    if raw_expiry is not None:
        if not isinstance(raw_expiry, str):
            raise ValueError("expires_at must be an ISO-8601 timestamp or null")
        expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
    return EntitlementProfile(tier.strip(), limit, visibility, expiry)


class RegistrationInviteService:
    def __init__(self, database: object):
        self._database = database

    def create(
        self,
        profile: EntitlementProfile,
        *,
        max_redemptions: int = 1,
        expires_at: datetime | None = None,
    ) -> CreatedInvite:
        if max_redemptions <= 0:
            raise ValueError("max_redemptions must be positive")
        if expires_at is not None and expires_at.tzinfo is None:
            raise ValueError("invite expiry must include a timezone")
        code = secrets.token_urlsafe(32)
        invite_id = uuid7()
        profile_json = {
            "model_access_tier": profile.model_access_tier,
            "daily_token_limit": profile.daily_token_limit,
            "prompt_visibility": profile.prompt_visibility,
            "expires_at": profile.expires_at.isoformat() if profile.expires_at else None,
        }
        validated_profile = parse_entitlement_profile(profile_json)
        profile_json["model_access_tier"] = validated_profile.model_access_tier
        with UnitOfWork(self._database) as uow:
            uow.execute(
                "INSERT INTO registration_invites(invite_id,code_hash,entitlement_profile,"
                "max_redemptions,expires_at) VALUES (%s,%s,%s,%s,%s)",
                (
                    invite_id,
                    hash_invite_code(code),
                    Jsonb(profile_json),
                    max_redemptions,
                    expires_at,
                ),
            )
        return CreatedInvite(invite_id, code)

    def consume_in_uow(self, uow: UnitOfWork, code: str) -> tuple[UUID, EntitlementProfile]:
        row = uow.execute(
            "SELECT invite_id,entitlement_profile,max_redemptions,redemption_count,"
            "expires_at,revoked_at FROM registration_invites WHERE code_hash=%s FOR UPDATE",
            (hash_invite_code(code),),
        ).fetchone()
        now = datetime.now(UTC)
        if (
            not row
            or row["revoked_at"] is not None
            or (row["expires_at"] is not None and row["expires_at"] <= now)
            or row["redemption_count"] >= row["max_redemptions"]
        ):
            raise InvalidRegistrationInvite("registration invite is unavailable")
        try:
            profile = parse_entitlement_profile(row["entitlement_profile"])
        except (TypeError, ValueError) as exc:
            raise InvalidRegistrationInvite("registration invite is unavailable") from exc
        return row["invite_id"], profile

    @staticmethod
    def record_redemption_in_uow(uow: UnitOfWork, invite_id: UUID) -> None:
        result = uow.execute(
            "UPDATE registration_invites SET redemption_count=redemption_count+1,"
            "updated_at=now() WHERE invite_id=%s AND redemption_count < max_redemptions",
            (invite_id,),
        )
        if result.rowcount != 1:
            raise InvalidRegistrationInvite("registration invite is unavailable")
