# W5-C Governed model invocation implementation report

## Baseline and dependencies

- Starting HEAD: `18e0feb0d44fe306708dba7f4a7411eef97a5f3c` on `main`, with a clean worktree.
- W5-A dependency: `c0b050a0f7fac2e405b24ceb298d310c5aa2468a`.
- W5-B dependency: `18e0feb0d44fe306708dba7f4a7411eef97a5f3c`.
- This change is limited to governed provider invocation, immutable input snapshots,
  endpoint access tiers, budget integration, and compact Trace references. It does not add
  prompt-review APIs/UI, a generic policy service, approvals, or a new Agent runtime.

## Invocation sequence

Before W5-C, `ResourcePool` reserved only the per-Run budget from a service held in a
ContextVar, then called `ModelClient.generate()`. The client built URL, headers, and payload
immediately before HTTP. A provider error released the reservation even when provider receipt
could not be disproved.

The production sequence is now:

1. `ModelCallContext` supplies Account, Run, stable operation/phase, execution attempt,
   final-response flag, and a logical-call ordinal. It contains no injected service.
2. `ResourcePool` resolves current Account liveness/entitlement for each candidate and checks
   the candidate access tier.
3. `ModelClient.prepare_request()` performs provider conversion, default max-output selection,
   tool conversion, stream selection, and `extra_body` merge.
4. `SnapshotRepository.freeze()` persists the exact credential-free provider body and its
   semantic hash.
5. `ModelBudgetCoordinator.reserve()` atomically reserves Account/day and Run dimensions and
   binds the Account ledger row to the snapshot.
6. `ModelClient.send_prepared()` verifies the frozen request and sends that body, constructing
   authentication headers only at dispatch.
7. ResourcePool settles provider/measured usage, or the full reservation as `estimated` when
   dispatch was entered and receipt cannot be disproved.
8. A fallback candidate repeats steps 2-7 with a distinct operation and snapshot under the
   same logical `model_call_id`.

The snapshot precedes reservation so a successful reservation always points to durable input.
A denied reservation leaves an input snapshot but creates no provider-dispatch evidence. No
network operation occurs in a database transaction.

## Prepared request and snapshot contract

`PreparedModelRequest` is frozen and contains only `endpoint_id`, provider, model, API format,
URL, recursively immutable payload, serializer version, and an internal payload digest. It has
no API key, headers, cookies, or credentials. `send_prepared()` rejects endpoint/format/model/
serializer substitution and verifies payload immutability before creating dispatch headers.

Migration `039_model_input_snapshot.sql` adds append-only `model_input_snapshots`. A composite
`(account_id, run_id)` foreign key proves Run ownership. The per-attempt operation is unique,
fallback attempts are unique within a model call, and Account/day ledger `snapshot_id` now has
a restrictive foreign key. Worker privileges are SELECT/INSERT only; no runtime role may
update or delete frozen snapshots.

Canonical hashing is SHA-256 over sorted, compact, UTF-8 JSON containing hash version,
serializer version (`hpagent-provider-request-v1`), endpoint, provider, model, API format, and
the exact provider request body. Display timestamps are excluded. Any message, tool, effective
parameter, endpoint, model, API format, or serializer change changes semantic identity.
Idempotent insertion returns the existing row only when the same operation has the same hash.

## Identity, access tier, and accounting

A logical call uses UUIDv5 over Account, Run, caller operation, phase, Activity execution
attempt, and call ordinal. Provider operation IDs additionally bind logical call, execution
attempt, fallback ordinal, and endpoint identity and remain below the 200-character ledger
limit. Concurrent contexts remain isolated by one ContextVar.

`ModelEndpoint.access_tier` defaults to `standard` and flows through model YAML parsing,
CredentialManager's sanitized metadata, and ResourcePool registration. `owner` Accounts may
use every configured tier; other Accounts may use only an exact tier match. An ineligible
candidate is skipped before preparation, snapshot, reservation, or dispatch, so fallback never
silently upgrades access.

Reservations estimate the converted provider messages and tools and use the effective prepared
`max_tokens`. Settlement updates Account/day and Run ledgers in the W5-B coordinator's single
transaction.

| Outcome | Dispatch evidence | Accounting |
|---|---|---|
| preparation/configuration failure | not entered | no reservation, or release if failure follows reserve |
| entitlement/tier/quota denial | not entered | no provider charge; quota reservation is absent/rolled back |
| successful response with usage | entered | settle actual values with provider provenance |
| successful response without usage | entered | settle locally estimated actual values |
| valid response exceeds latency policy | entered and consumed | first attempt remains settled; fallback gets a new snapshot/reservation |
| timeout/transport/HTTP/parsing failure after POST starts | receipt uncertain | conservatively settle full reservation as `estimated`, then permit fallback |
| local programming error before dispatch | not entered | release reservation and propagate |

This proves the body HpAgent prepared and handed to HTTP dispatch. It does not claim provider
exactly-once execution.

## Production paths audited

- ReAct/chat decision, forced final, and plan-step/final synthesis: governed phases from
  `DurableAgentActivities.model_decision`.
- Plan creation and replan: governed `planning` phase.
- Plan evaluation: governed `plan_evaluation` phase.
- Memory recall-query rewrite: governed `memory_query_rewrite` phase.
- ActionRuntime's model-based tool-result summary: governed `tool_result_summary` phase.
- Fixed Research workflow synthesis through `ResourcePoolResearchSynthesizer`: governed
  `research_synthesis` phase after resolving the Research Run owner.
- Web Artifact generation: governed `artifact_generation` phase using the source message's
  durable producing Run and Account.
- Repository search found no other production `ResourcePool.generate()` path outside these
  scopes. Embedding/reranking/image provider code is separate non-LLM resource machinery and
  was not redirected into this contract.

Trace remains a non-authoritative projection. `LLMCall` metadata now allowlists only compact
model-call/snapshot/hash/fallback/outcome references plus existing operational fields. Prompt
bodies, messages, tools, headers, and credentials remain rejected by the sanitizer.

## Validation evidence

- `.venv/bin/pytest -q test/test_model_input_governance.py test/test_model_governance.py test/test_model_configuration.py test/test_run_budget.py test/test_account_daily_budget_contract.py`:
  **24 passed**.
- Fresh isolated PostgreSQL database, migrations 001-039, then
  `.venv/bin/pytest -q test/web_persistence/test_model_input_snapshot.py`:
  **2 passed**. The temporary database was removed afterward.
- Fresh isolated PostgreSQL database, migrations 001-039, then
  `.venv/bin/pytest -q test/web_persistence/test_schema_contract.py test/web_persistence/test_permissions.py`:
  **11 passed**. The temporary database was removed afterward.
- `.venv/bin/pytest -q test/test_research_domain.py test/test_research_temporal_contract.py test/test_web_artifacts.py test/test_trace_events.py test/test_durable_agent_temporal_integration.py`:
  **41 passed, 5 skipped** (the skips require `TEMPORAL_HOST`).
- `.venv/bin/pytest -q -m 'not postgres' test`:
  **463 passed, 23 skipped, 244 deselected**, one upstream Starlette/httpx deprecation warning.
- Focused Ruff over all changed Python source and tests: **passed**.
- Python `compileall` over `src`: **passed**.
- `git diff --check`: **passed**.

An attempted combined PostgreSQL run against the already-running shared development stack was
stopped after four passing tests because its fixture TRUNCATE competed with local services for
table locks. It is not counted as passing evidence. The new migration and snapshot/account/run
binding tests were rerun successfully in the isolated database above.

## W5-D follow-ups

- Add Account-authorized snapshot review/query APIs honoring `prompt_visibility`.
- Redact or summarize snapshot bodies at the API boundary without changing durable authority.
- Add the prompt-review UI and its explicit loading/error/authorization states.
- W5-E remains responsible for final cross-surface acceptance and documentation reconciliation.
