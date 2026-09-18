# W5 implementation closure report

## Verdict, scope, and non-goals

**W5 closure verdict: CLOSED.** W5 closes the implemented Account Access Governance,
Account/day quota, immutable model-input snapshot, governed provider invocation, and prompt
visibility capability. It does not claim the broader R2.1 durable human Model Review target.
No universal policy engine, billing system, Account admin dashboard, Agent redesign,
Research-to-Agent conversion, or generic RBAC framework was added.

The closure baseline was clean `main` at `0a645bc8d1929a5b740b6b02259368f443bab338`.
Prerequisite commits are:

- W5-A `c0b050a` — `feat(w5-a): add invite-gated account entitlements`
- W5-B `18e0feb` — `feat(w5-b): add atomic account model quotas`
- W5-C `cdf4861` — `feat(w5-c): freeze and govern model provider requests`
- W5-D `0a645bc` — `feat(w5-d): expose governed model input projections`
- W5-E — included in the final closure commit to avoid a self-referential report edit

## Schema and ownership map

W5 adds three ordered migrations:

- `037_account_access_governance.sql`: `registration_invites` and one
  `account_entitlements` row per Account; existing Accounts receive an owner entitlement.
- `038_account_daily_model_budget.sql`: UTC Account/day summary and immutable operation ledger;
  atomic coordination with the existing `run_budget_*` tables.
- `039_model_input_snapshot.sql`: append-only `model_input_snapshots`, Account/Run ownership,
  provider-attempt uniqueness, hash/version fields, and ledger snapshot reference.

`accounts.status` remains liveness authority. `RegistrationService` owns invite-gated Web
provisioning; the operator bootstrap owns unrestricted owner provisioning. `EntitlementService`
owns current access reads. `AccountDailyBudgetService`, `RunBudgetService`, and
`ModelBudgetCoordinator` own transactional accounting. `ResourcePool` is the shared governed
invocation boundary; `ModelClient` owns provider serialization and credential-bearing dispatch;
`SnapshotRepository` owns immutable input records. `SnapshotQueryRepository` enforces ownership
at SQL boundaries. Trace and the Web panel are non-authoritative projections.

## Provisioning and invocation protocols

Invite registration locks and validates the digest-only invite, creates Account, Web identity,
credential, and concrete entitlement, increments bounded redemption, and commits once. Duplicate
or invalid registration rolls the transaction back. Operator bootstrap creates Account and owner
entitlement in one transaction. Runtime reads Account status and entitlement only; it never reads
the invite code or registration profile.

For every provider attempt, `ModelCallContext` supplies Account, Run, durable caller operation,
phase, execution attempt, final-response flag, and a logical-call ordinal. The logical call UUID
binds those values. Each candidate operation additionally binds fallback ordinal and endpoint.
Activity replay therefore maps the same durable identity to the same request hash, while a changed
candidate/request cannot reuse that identity without `SnapshotConflict`.

The source-true sequence is:

```text
Invite / operator provisioning
        -> Account + Entitlement
        -> shared ResourcePool invocation
        -> Account liveness + endpoint-tier eligibility
        -> PreparedModelRequest (effective provider body)
        -> immutable ModelInputSnapshot
        -> atomic Account/day + RunBudget reservation
        -> credential-bearing provider dispatch
        -> provider/measured/estimated settlement or proved-pre-dispatch release
        -> compact Trace ref + owned visibility projection
```

`PreparedModelRequest` freezes endpoint, provider, model, API format, URL, serializer version,
and the exact credential-free request body. Canonical SHA-256 binds hash version, serializer,
endpoint/provider/model/format, and sorted compact UTF-8 JSON body. Authentication headers are
constructed only inside dispatch. Fallback creates a separate attempt operation and snapshot;
endpoint eligibility is checked before preparation, snapshot, reservation, or dispatch.

Provider usage settles with `provider`; valid responses without usage use local `measured` usage.
Once HTTP dispatch is entered, timeout/transport/HTTP/parsing failure is `uncertain`: the full
reservation settles as `estimated`, and any fallback is a separate attempt. Definitely
pre-dispatch programming/configuration failure can release or avoid reservation. This is
conservative at-least-once accounting, not provider exactly-once execution.

## Prompt visibility and Trace

Current entitlement controls projection without rewriting the snapshot:

- `none`: list exposes only snapshot ID, content hash, and model-call ID; detail is denied.
- `summary`: adds allow-listed operational metadata and message/tool counts, never prompt text.
- `full_safe`: adds the stored canonical provider request body.

All modes preserve the same snapshot ID/hash. SQL queries include authenticated `account_id`, so a
foreign Account cannot enumerate or read a snapshot. Trace stores compact refs and operational
metadata only. The Trace panel lazily requests detail, renders explicit unavailable/summary/full
states, and treats JSON as text. Sentinel regressions prove prompt content and credentials are
absent from summary and Trace.

## Repository-wide model-call reachability

| Call site | Run kind / phase | Production | Shared pool | Entitlement / Account quota / RunBudget | Snapshot + Trace ref | Visibility | Status / rationale |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BrainEngine.generate_chat` from `model_decision` | Agent ReAct, plan step / `decision` | yes | yes | yes / yes / yes | yes / yes | yes | governed |
| forced final and plan final synthesis | Agent / `final` | yes | yes | yes / yes / yes | yes / yes | yes | governed |
| plan creation and replan | Agent / `planning` | yes | yes | yes / yes / yes | yes / yes | yes | governed |
| plan evaluation | Agent / `plan_evaluation` | yes | yes | yes / yes / yes | yes / yes | yes | governed |
| recall-query rewrite | Agent bootstrap / `memory_query_rewrite` | yes | yes | yes / yes / yes | yes / compact rewrite Trace | yes | governed auxiliary call, explicitly not human-reviewed |
| ActionRuntime tool-result summarization | Agent / `tool_result_summary` | yes | yes | yes / yes / yes | yes / enclosing Trace | yes | governed |
| `ResourcePoolResearchSynthesizer` | fixed Research / `research_synthesis` | yes | yes | yes / yes / yes | yes / Research operational trace | yes | governed using Research Run owner |
| `WebArtifactGenerator` via build service | Artifact / `artifact_generation` | yes | yes | yes / yes / yes | yes / build trace | yes | governed using producing Run owner |
| `scripts/check/models.py` native/direct | operator diagnostic | no | optional/direct | no | no | no | deliberately outside production claims |
| test fakes and benchmark helpers | test/benchmark | no | varies | no production claim | no production claim | no | excluded as roots |
| embedding/reranker | vector/ranking resource | yes | no | outside W5 LLM contract | no | no | separate non-generative provider machinery |
| Research discovery/fetch, MCP, Gotenberg | search/tool/document HTTP | yes | no | not LLM dispatch | no | no | `httpx` alone is not a model call |

Repository searches covered `.generate(`, `ResourcePool`, `ModelClient`, `model_budget_scope`,
`ModelCallContext`, `synthesize(`, and model-adjacent `httpx` use. No additional production
generative-model root was found.

## G12 captured-body and behavioral evidence

`test/test_g12_capturing_provider.py` replaces the HTTP client with a capturing provider and runs
the real `ResourcePool -> ModelClient.send_prepared -> HTTP post(json=...)` path. For both Anthropic
and OpenAI it proves the captured JSON object equals the snapshot payload and recomputes the stored
canonical hash from that captured body. The cases include tools, effective default `max_tokens`,
`extra_body`, a ReAct `decision` context, compact result refs, and credential exclusion. Existing
governance tests cover fallback A/B identity and hash, access-tier denial, quota denial before
dispatch, conservative uncertainty settlement, and provider settlement. Production reachability
tests cover planning/evaluation/final, memory rewrite, Research, and Artifact scopes.

PostgreSQL acceptance covers finite and unlimited Accounts, limit changes, expired entitlement,
disabled Account, UTC rollover, atomic Account/Run rollback, release and settlement replay,
snapshot replay conflicts, Account/Run binding, current visibility changes, foreign ownership,
and Trace sentinel exclusion. W1 lease/fencing code was not changed by W5.

## Validation executed at closure

- Capturing provider: `.venv/bin/pytest -q test/test_g12_capturing_provider.py` — **2 passed**.
- Focused governance/call-site regression command covering model input/governance/configuration,
  budgets, Research, Artifact, Trace, and durable Agent integration — **67 passed, 5 skipped**;
  skips require `TEMPORAL_HOST`.
- The broad non-PostgreSQL evidence in `W5_E_python_validation.txt` contains the completed summary:
  **465 passed, 23 skipped, 249 deselected**. Skipped cases are not counted as passed.
- Disposable in-memory PostgreSQL 16, fresh migrations 001–039, API and Worker roles; focused
  provisioning/quota/snapshot/visibility/schema/permission suites — **66 passed** with one
  upstream Starlette/httpx deprecation warning; `W5_E_postgres_validation.txt`.
- `npm test -- --maxWorkers=1` — **14 files, 83 tests passed**;
  `W5_E_frontend_test_validation.txt`.
- `npm run lint`, `npm run typecheck`, and `npm run build` — **passed**; Vite emitted its existing
  large-chunk advisory.
- Focused Ruff, Python compileall, and `git diff --check` — **passed**.

Focused Ruff and Python compileall pass. The repository-wide Ruff invocation reports 57
pre-existing errors in untouched legacy modules and is not represented as a W5 failure. The
historical Phase 2.2 validator cannot run against current HEAD because it tries to parse the
retired `src/orchestration/web_workflow.py`; its machine-readable R2.1 records were therefore
preserved rather than regenerated as misleading current-state evidence.

No live model API was called. The PostgreSQL container was disposable and did not touch application
data; it was removed after the suite. Playwright was not rerun: W5-D adds no standalone navigation
flow, and the lazy Trace detail behavior is covered by the 83-test frontend suite while API
ownership/visibility is covered against fresh PostgreSQL.

## G12 gate verdict

G12-01 through G12-16 pass for the explicitly implemented W5 subset: atomic provisioning and
entitlement semantics; atomic dual-budget accounting; pre-dispatch quota/tier denial; preparation
before freeze; captured-body/hash equality; per-fallback snapshots; credential exclusion;
conservative uncertainty; intended Agent/Research/auxiliary reachability; owned three-mode
visibility; compact non-authoritative Trace; unchanged W1 fencing; relevant regressions; and
source-true documentation.

## Superseded historical scope

The original ACD-17 combined snapshots with frozen `AuthorizationPolicy`, durable human review,
review records/commands, approval binding, wait/reacquire behavior, and approval UI. That historical
bundle was superseded by the final W5 product decision: human-review capabilities were intentionally
removed, not deferred. Final ACD-17 covers snapshot/governed invocation/entitlement/account quota/
prompt visibility/observability. Workspace Query is outside W5. Research remains a fixed Workflow.
