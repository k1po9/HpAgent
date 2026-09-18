# W5-D Prompt visibility API and Trace integration report

## Baseline and dependencies

- Starting HEAD: `cdf48616feee79cab0769d29210f86d46cc7c17d` on `main`, with a clean worktree.
- W5-A dependency: `c0b050a0f7fac2e405b24ceb298d310c5aa2468a`.
- W5-B dependency: `18e0feb0d44fe306708dba7f4a7411eef97a5f3c`.
- W5-C dependency: `cdf48616feee79cab0769d29210f86d46cc7c17d`.
- Scope is the read/query/UI visibility boundary only. No snapshots are regenerated or
  mutated, no model requests are sent, and no second Trace or prompt authority was added.

## Query and API contracts

`SnapshotQueryRepository` performs both snapshot reads with `account_id` in SQL. Run listing
also joins the snapshot to its owning Run and first verifies the Account-owned Run, while
detail lookup joins on the stored Account/Run ownership pair. A missing and a foreign Run or
snapshot therefore produce the existing `404 resource_not_found` response without exposing
cross-Account existence.

The final authenticated read routes are:

- `GET /api/v1/runs/{run_id}/model-inputs`: returns `{visibility, items}`. It never embeds
  provider bodies. `full_safe` uses the same summary list representation as `summary` so
  callers must explicitly load detail.
- `GET /api/v1/model-inputs/{snapshot_id}`: returns `{visibility, model_input}` for `summary`
  and `full_safe`. For `none`, it returns `403 model_input_unavailable` without content-derived
  details.

Both routes derive Account identity from the existing `AuthContext`. Current entitlement is
resolved for every request through W5-A's `EntitlementService`; invite/profile data is never
consulted. Expired or missing entitlement resolves to `none`. A disabled Account cannot retain
an authenticated Web session, matching the existing W5-A authentication semantics. Existing
common middleware continues to set `Cache-Control: no-store`.

## Frozen projection allow-lists

Every visible representation preserves the same `snapshot_id`, hex `content_hash`, and
`model_call_id` from the immutable W5-C row.

- `none` list item: `snapshot_id`, `content_hash`, `model_call_id` only. Detail returns the
  stable unavailable error and no body, counts, snippets, or hidden-content hints.
- `summary`: the three identity fields plus `phase`, `fallback_attempt`, `endpoint_id`,
  `provider`, `model`, `api_format`, `created_at`, `message_count`, and `tool_count`.
- `full_safe`: exactly the summary projection plus the stored canonical
  `provider_request_body`.

Counts are deterministic local counts over recognized arrays in the stored JSON object. No
Conversation, Transcript, Memory, file, or system-prompt reconstruction occurs. The W5-C body
is credential-free by construction; W5-D does not add redaction theater over it.

## Existing Trace Debug Panel integration

- `web/src/api/types.ts` and `web/src/api/resources.ts` add the detail contract and narrow GET.
- `TraceTree.tsx` marks events carrying `snapshot_id` with a Model Input affordance.
- `TraceDetail.tsx` shows snapshot/hash correlation, phase, provider/model, counts, and fallback
  attempt. Summary and unavailable states are explicit. `full_safe` JSON is serialized into a
  React `<pre>` as data; no HTML injection API is used.
- `traceStore.ts` retains current tree selection/loading behavior and owns a per-snapshot lazy
  query cache. It issues no detail request until the user opens Model Input and keeps fallback
  snapshots independently addressable.

No provider body was added to ordinary Run DTOs, SSE, or eagerly loaded Trace responses.

## Trace authority and sentinel proof

W5-C's Trace sanitizer remains unchanged and allowlists compact `snapshot_id`, `content_hash`,
`model_call_id`, phase/fallback, endpoint/model/provider, and outcome metadata for `LLMCall`.
It does not allow `provider_request_body`, messages, tools, headers, or credentials. The new
PostgreSQL API regression stores `W5-D-PROMPT-MUST-NOT-LEAK` in the canonical snapshot, proves
the exact body is returned by `full_safe`, and proves the sentinel and body field are absent
from both summary output and the Trace API payload.

## W5-A Web regression check

The existing registration UI already contains the invite input, sends `invite_code`, renders
the stable `registration_invite_invalid` response, and retains automatic session setup. Its
existing frontend regression tests and backend registration/login/QQ-binding tests pass. No
W5-A repair was required and no account-admin UI was added.

## Validation evidence

Successful final-state commands:

- Isolated PostgreSQL database, migrations 001-039, API/Worker roles:
  `pytest -q test/web_api/test_model_observability.py test/web_api/test_identity_self_service.py test/web_persistence/test_model_input_snapshot.py`:
  **13 passed**, one upstream Starlette/httpx deprecation warning. The explicit temporary
  database was dropped afterward.
- `.venv/bin/pytest -q test/test_trace_events.py test/test_model_input_governance.py test/test_model_governance.py`:
  **23 passed**.
- `npm test -- --maxWorkers=1`: **14 files passed, 83 tests passed**. One worker was selected
  because an earlier default-pool attempt timed out while starting four workers and executed
  zero tests.
- `npm run lint`: ESLint and Prettier checks passed.
- `npm run typecheck`: TypeScript no-emit check passed.
- `npm run build`: TypeScript project build and Vite production build passed; Vite emitted the
  existing large-chunk advisory.
- Focused Ruff check over changed Python source/tests: passed.
- Python `compileall` over changed source/tests: passed.
- `git diff --check`: passed.

## W5-E integration items still open

- Reconcile the final W5-A through W5-D architecture and API documentation.
- Run final cross-surface acceptance/closure evidence, including the canonical deployment/E2E
  matrix required by W5-E.
- Decide whether the model-input routes need inclusion in any public API reference generated
  during final documentation reconciliation.
