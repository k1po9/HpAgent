# W5-A Account Access Provisioning implementation report

## Baseline and scope

- HEAD before work: `05960d1ef2cb35be0fb5886597ec8b82ee19fc20` on `main`.
- The worktree was clean before edits.
- Scope is W5-A only. No quota enforcement, model snapshots/reviews, ResourcePool changes,
  prompt APIs, or W5-B work was added.

## Source inspected

- Phase 2.2 review summary, decision register, target boundaries, delivery gates, and model
  input review contract.
- W1-A through W1-C, W2-A/B/C/E, W3-A through W3-E, and W4-A/B/C phase 3 reports.
- Account registration, PostgreSQL identity resolution, bootstrap, normalization,
  credential and identity-binding services, UnitOfWork, Web API models/app/config.
- Migrations `001_phase_a_schema.sql`, `012_self_service_identity.sql`, and the then-latest
  `036_qq_delivery.sql`, including application role grants.
- Account/bootstrap/Web API PostgreSQL tests, operator scripts, benchmark provisioning,
  frontend registration form, API client, and their tests.

The design-time facts remained true at baseline: registration used one UnitOfWork for the
Account/binding/credential writes; `PostgresAccountService` was read-only;
`bootstrap_identity()` could create an Account directly; and `RegisterRequest` did not carry
an invite.

## Schema and grants

`037_account_access_governance.sql` adds:

- `registration_invites`, with a unique binary code digest, JSON entitlement profile,
  positive redemption limit, bounded redemption count, optional invite expiry/revocation,
  timestamps, and an availability index.
- `account_entitlements`, with exactly one row per Account, non-empty access tier, nullable
  positive daily token limit, constrained prompt visibility, optional entitlement expiry,
  version, optional provisioning-invite FK, timestamps, and invite/expiry indexes.
- A deterministic backfill of every existing Account to tier `owner`, no daily cap, and
  `full_safe` prompt visibility.

There is no entitlement enabled/status column; `accounts.status` remains the Account liveness
authority. The API role can read/insert entitlements and read/update invites, which is the
minimum needed for registration. The Worker receives read-only entitlement access. Neither
role receives delete or invite-creation permission from this migration.

## Invite convention and operator path

`RegistrationInviteService.create()` generates a 32-byte `secrets.token_urlsafe` secret and
persists only its SHA-256 digest, matching the repository's high-entropy token/digest pattern.
The comparison is performed by indexed digest lookup; plaintext is never stored. The service
validates the concrete profile before insertion.

`scripts/operations/create-registration-invite.py` uses `MIGRATION_DATABASE_URL`, accepts the
access tier, daily cap, prompt visibility, entitlement expiry, invite expiry, and maximum
redemptions, and prints the plaintext secret once at creation. No invite-management REST API
or UI was added.

## Registration transaction and errors

`RegistrationService.register(username, password, invite_code)` now performs one transaction:

1. Hash the submitted secret, select the invite `FOR UPDATE`, and reject missing, expired,
   revoked, or exhausted rows with `InvalidRegistrationInvite`.
2. Validate and expand the invite profile.
3. Insert the Account, Web identity binding, and Argon2 Web credential.
4. Insert the concrete Account entitlement with `provisioned_by_invite_id`.
5. Increment redemption count with a bounded update and commit.

A username uniqueness failure rolls back the locked invite without incrementing it. Any
entitlement or redemption failure rolls back all Account writes. The Web API maps every
unavailable-invite state to the same non-secret-leaking `registration_invite_invalid` error.
Successful registration and automatic session establishment are unchanged.

The strict request DTO and browser client/form now require `invite_code`. Frontend tests prove
the request contains the field and the safe server error is rendered as text.

## Account creation audit

- Web self-registration: invite-gated transaction described above.
- `bootstrap_identity()`: operator/admin path; when it creates an Account it now creates an
  unrestricted owner entitlement in the same PostgreSQL transaction. Existing binding and
  no-merge semantics are unchanged.
- Agent-strategy benchmark and Web E2E setup: changed to create operator invites and use the
  canonical registration service.
- Outbox recovery benchmark: its direct benchmark Account seed now inserts a benchmark
  entitlement in the same transaction.
- Remaining direct `INSERT INTO accounts` occurrences are test fixtures that intentionally
  construct specific database states. Production `PostgresAccountService` remains SELECT-only;
  its existing read-only identity-resolution tests pass unchanged.

## Entitlement reader

`EntitlementService` is a dedicated read-only service returning immutable typed values. Its
result state explicitly distinguishes missing/disabled Account, missing entitlement, expired
entitlement, and valid entitlement. This responsibility was not added to
`PostgresAccountService`.

## Validation evidence

Final commands and results:

- `pytest -q test/web_persistence/test_account_access_governance.py test/web_persistence/test_identity_self_service.py test/web_persistence/test_bootstrap_identity.py test/web_persistence/test_postgres_account_service.py test/web_api/test_identity_self_service.py`
  with isolated PostgreSQL migration/API/Worker roles: **34 passed**, one upstream Starlette
  deprecation warning.
- `pytest -q test/web_persistence/test_permissions.py test/web_persistence/test_schema_contract.py`
  against the same isolated PostgreSQL database: **11 passed**.
- `npm test -- --run src/api/client.test.ts src/components/LoginForm.test.tsx`: **15 passed**.
- `npm run build`: TypeScript and Vite production build passed; Vite emitted its existing
  large-chunk advisory.
- Focused Ruff check over changed Python source/tests/scripts: passed.
- `git diff --check`: passed.

The PostgreSQL tests exercised valid provisioning, duplicate-name rollback, all unavailable
invite states, concurrent max-redemption enforcement, all entitlement reader states,
bootstrap provisioning, Web errors/session behavior, and the existing read-only identity
resolver. The migration SQL contract test covers deterministic existing-Account backfill.

## Deviations and remaining W5-B dependencies

- Migration number `037` matched the design-time expectation because HEAD still ended at 036.
- The suggested files were used without introducing a generic repository layer. Invite
  consumption is the narrow caller-owned-UoW primitive required for atomic registration.
- SHA-256 is unpeppered by design because invite secrets have 256 bits of generated entropy;
  unlike short QQ challenge codes, they do not need a shared online pepper. This also follows
  the existing random session-token digest pattern.

W5-B still owns runtime entitlement enforcement, Account/day usage accounting, model input
snapshot/review authorization, durable review waits, and any policy/snapshot projection. W5-A
only establishes provisioning data and its atomic creation paths.
