#!/usr/bin/env python3
"""Create a registration invite and print its one-time plaintext secret."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from account.invite_service import EntitlementProfile, RegistrationInviteService  # noqa: E402


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an optional registration invite with an Account entitlement profile")
    parser.add_argument("--profile", required=True, help="Model access tier")
    parser.add_argument("--daily-token-limit", type=int)
    parser.add_argument(
        "--prompt-visibility", choices=("none", "summary", "full_safe"), default="summary"
    )
    parser.add_argument("--entitlement-expires-at", type=_timestamp)
    parser.add_argument("--max-redemptions", type=int, default=1)
    parser.add_argument("--invite-expires-at", type=_timestamp)
    parser.add_argument("--database-url", default=os.getenv("MIGRATION_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("set MIGRATION_DATABASE_URL or pass --database-url")
    created = RegistrationInviteService(args.database_url).create(
        EntitlementProfile(
            args.profile,
            args.daily_token_limit,
            args.prompt_visibility,
            args.entitlement_expires_at,
        ),
        max_redemptions=args.max_redemptions,
        expires_at=args.invite_expires_at,
    )
    print(f"invite_id={created.invite_id}")
    print(f"invite_code={created.code}")
    print("Store the invite code now; only its SHA-256 digest was persisted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
