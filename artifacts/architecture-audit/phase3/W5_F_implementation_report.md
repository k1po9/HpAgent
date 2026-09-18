# W5-F · Model Access Governance Closure Corrections

## Baseline and source review

- Baseline: `38e8f8c6927c84df238dc2d90ed74946f2f68728` on clean `main`.
- Re-read the Account entitlement, Account/day and Run budget, model context/pool/client/request
  snapshot/credential paths; Agent, Research, and Artifact model call sites; snapshot projection,
  repository, and API query boundary; migrations 037–039; registration browser fixture/UI;
  CI workflow and Makefile; and the Phase 2.2 / W5 closure evidence requested by W5-F.
- Human Model Review, approve/reject, `AuthorizationPolicy`, and durable review wait remain removed
  product targets. Existing file-action approval and generic durable wait were not changed.

## Corrections

### F1 · Stable governance failures

A narrow `ModelGovernanceError` family now classifies deterministic denial separately from transient
provider failure. Stable public codes are:

- `account_model_entitlement_unavailable`
- `account_daily_model_budget_exhausted`
- `model_access_tier_denied`

`ResourcePool` raises entitlement denial before preparation/HTTP and distinguishes an all-tier-denied
chain from a chain whose eligible providers failed. Agent decision, planning, plan evaluation,
memory rewrite, tool-result summary, Research synthesis, and Artifact generation use one classifier
instead of treating these failures as retryable `model_unavailable`. Provider transport timeout and
fallback behavior remains unchanged.

### F2 · Entitlement TOCTOU

The resolved entitlement version and endpoint access tier now cross the provider-attempt boundary
into `ModelBudgetCoordinator.reserve()`. Inside the same Account/day + Run reservation UoW, the
Account active state, entitlement existence/expiry/version, endpoint-tier permission, and daily
quota are rechecked before either reservation commits. Version change, disable/expiry, or downgrade
therefore denies dispatch and rolls back both ledgers.

### F3 · Snapshot semantics and observability

`ModelInputSnapshot` remains an immutable canonical provider request candidate, not proof of HTTP
receipt. Migration `040_w5_closure_corrections.sql`:

- adds nullable `resolved_url` because legacy pre-production rows cannot be truthfully backfilled;
- removes unused review-era `supersedes_snapshot_id`;
- constrains new URL values to credential/query/fragment-free HTTP(S) targets.

New writes require canonical resolved URLs and semantic hash v2 binds that URL. Application
canonicalization rejects userinfo, query strings, and fragments so credentials cannot enter the
snapshot. Snapshot queries deterministically project `not_dispatched`, `reserved_or_in_flight`,
`succeeded`, or `uncertain` from the authoritative usage ledger; quota-denied snapshots therefore
remain visible without implying dispatch.

### F4 · CI failures

- Prompt visibility now has the explicit `Literal["none", "summary", "full_safe"]` domain type;
  the query boundary returns that type without `Any`, ignores, or unsafe casts.
- The E2E backend creates a fresh one-redemption invite through `RegistrationInviteService`, writes
  its plaintext only to mode-0600 `web/.e2e-runtime/registration-invite`, and the registration test
  reads and deletes it before exercising the real `/auth/register` flow. The runtime directory is
  gitignored. The production invite gate is unchanged.

### F5 · Documentation reconciliation

Required Phase 2.2 files now explicitly distinguish historical R2.1 review design from final W5.
Historical ACD-17/G12 text is retained as evidence, while final ACD-17 and G12 describe model access
governance/input observability. Human review is marked superseded/removed, not future work. The W5-E
report now records the existing non-PG evidence accurately as 465 passed, 23 skipped, 249 deselected.

## Validation

| CI job | Local equivalent | Result |
| --- | --- | --- |
| lint-and-typecheck | `make PYTHON=.venv/bin/python lint typecheck` | PASS |
| existing-unit-tests | `PYTHONPATH=src .venv/bin/python -m pytest -m "not postgres" test` | PASS: 465 passed, 23 skipped, 250 deselected |
| phase-a-postgres-contract-tests | fresh PostgreSQL 16, migrations 001–040; `pytest -m postgres test/web_persistence` | PASS: 188 passed, 19 skipped |
| phase-b-api-contract-tests | `pytest -m postgres test/web_api` | PASS: 31 passed, 12 skipped, 19 deselected |
| phase-c-isolation-gate | context/workspace unit command | PASS: 15 passed |
| phase-d-temporal-fault-gate | frozen Agent segment replay | PASS: 5 passed; real Temporal fault suite NOT RUN locally |
| phase-e-frontend-checks | lint, typecheck, Vitest, build | PASS: 14 files / 83 tests; build PASS |
| phase-e-browser-e2e | `make e2e`; focused `auth.spec.ts` | FAIL locally: focused auth passed, but full suite remained unstable in local Chromium (latest CI-mode run: 4 passed, 1 flaky, 8 failed) |
| phase-f-gate | CI-listed unit command | PASS: 22 passed |
| phase-g-gateway-smoke | `scripts/check/gateway-smoke.sh` | NOT RUN locally |

Additional focused evidence:

- Governance/model-input/capturing provider: 11 passed.
- Entitlement version/tier atomic-reserve correction: 1 passed.
- Snapshot URL/hash persistence: 2 passed.
- Owned model-observability projection: 1 passed.
- Fresh migration head: `040_w5_closure_corrections.sql`.

## Known limitations and verdict

The required registration flow itself passed against the real API, fresh PostgreSQL, and the
one-redemption invite created by `RegistrationInviteService`. The full browser job did not pass on
this host: even the first static accessibility assertion timed out while its failure snapshot already
contained the expected heading, and later tests suffered similar browser-action stalls. Repeated
runs used fresh PostgreSQL databases and a disposable Redis. Because the explicit `make e2e` gate is
not green, this report does not claim closure. CI jobs explicitly marked NOT RUN are not local passes.

W5 closure verdict: **OPEN**.

Blocker: obtain a complete green `make e2e` result (locally or in GitHub CI) without weakening the
production invite gate. No W5-F commit is permitted while this blocker remains.
