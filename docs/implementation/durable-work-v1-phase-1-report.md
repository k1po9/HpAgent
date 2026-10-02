# Durable Work V1 Phase 1 Implementation Report

Date: 2026-10-02. Scope: Work Domain Foundation only. No commit made.
Design authority: `docs/design/durable-work-v1-implementation-design.md`.

## Changed files

- Domain and persistence: `src/work_domain/{__init__,models,commands,persistence,completion}.py`, `src/run_domain/{__init__,models,admission,lifecycle}.py`, `src/conversation_domain/{commands,delivery,run_input,run_projection}.py`, `src/research_domain/{services,persistence,history}.py`, `src/research_activities/runtime.py`, `persistence/migrations/054_durable_work_foundation.sql`.
- Runtime and integration: `src/agent_activities/store.py`, `src/application/memory_retention.py`, `src/orchestration/{agent_lifecycle_workflow,research_workflow,run_lifecycle_contracts,web_dispatcher,web_reconciler,web_workers,worker}.py`, `src/tracing/repository.py`, `src/web_domain/{lifecycle,workflow_execution}.py`, `src/workspace/{catalog,discovery,file_scope,resources}.py`.
- HTTP/API: `src/web_api/{app,models,queries,sse,terminal_publisher}.py`.
- Frontend compatibility with the new API and Run status: `web/src/adapters/assistant-ui/runtime.ts`, `web/src/api/{resources,types}.ts`, `web/src/components/{ResearchOutputs,WorkspacePanel}.tsx`, `web/src/sse/{runFeed,runFeed.test}.ts`, `web/src/store/{workbench,workbench.test}.ts`.
- Tests and helpers: `test/work_domain/{conftest,test_foundation}.py`, `test/support/{__init__,work_fixtures,research_worker_process}.py`, `test/test_{durable_agent_contract,web_temporal_contract,w1c_cutover_contract}.py`, `test/web_api/test_{work_foundation,research_file_composition,research_terminal_cancel,sse_gateway,workspace_research_cancel_chain,workspace_v41_p2}.py`, `test/web_persistence/test_{outbox_and_lifecycle,phase_a_hardening,research_r0_r2,workspace_docx_chain,workspace_research_sigkill,workspace_v41_p2,workspace_v41_p4,workspace_v41_p4_temporal}.py`.
- Removed obsolete Task-specific schedule and non-chat-owner tests: `src/orchestration/research_schedule.py`, `test/test_research_schedule.py`, `test/web_persistence/test_research_schedule_temporal_integration.py`, `test/web_persistence/test_w1_source_data_plane.py`. The latter expected a `file_job` Run owner; Phase 1 only permits chat/work, while Generic Work execution belongs to Phase 3.

The design document under `docs/design/` was already present as an untracked input and was not edited in this implementation.

## Schema and API

- Migration 054 defines Work, immutable requirement revisions, Conversation links, append-only events, wakeups, typed continuation/checkpoint/completion receipt, and Work-owned Run identity. It replaces Research Task ownership, removes `tasks`/`task_id`, enforces chat/work owner exclusivity, fixed strategy and executor, immutable Run input, active Run uniqueness, deferred coordinator pointer consistency, Account-composite FKs, Work transitions, and explicit current-revision completion evidence. Run terminal success is `succeeded`; Message, Artifact, operation, and Temporal `completed` remain unchanged. Work event outbox rows need no Run. Resource subject, Research history, plan/source, and Workspace save intent identities now resolve through Work/Run/revision. API and worker roles have separate grants.
- `/api/v1/tasks` CRUD/trigger is removed. `/api/v1/works` provides create/list/get, revision, pause/resume/stop, advance, Conversation link, events, runs, and Work resource grant endpoints. Mutations share `WorkCommandService`, use idempotency keys and `If-Match` row versions; cross-account reads return 404. Research evidence/report queries remain under Run identity. `GET /api/v1/runs/{id}` retains the published-file projection for Work Research Runs. No second Task/Work responsibility path, migration flag, or legacy compatibility layer was added.
- Research plan, sources, evidence, claim/citation verification, report, history, and required Workspace save remain. A Research Run reads its fixed requirement revision, not a live mutable Task objective. Common Run lifecycle handles terminal facts; chat-only Message projection remains isolated from Work Runs.

## Verification

All PostgreSQL tests used a fresh isolated PostgreSQL 16 container and distinct migration/API/worker roles. The shared Compose business database was not migrated or cleared.

| Check | Result |
|---|---|
| `test/work_domain` | 24 passed (also included in final contract run) |
| Research persistence R0-R2 | 11 passed |
| Workspace V4.1 P4 Research/save | 5 passed across two targeted invocations |
| Phase A invariants, schema contract, Workspace P2 and DOCX | 33 passed |
| Research/Web API cancellation, file composition, Workspace P2 | 6 passed |
| Existing chat lifecycle, hardening, permissions, SSE without Redis | 42 passed, 12 skipped for missing Redis in that invocation |
| Work HTTP contract plus SSE with real Redis | 14 passed; no Redis skips |
| Final Work + durable/Temporal/cutover contracts | 79 passed |
| Frontend Run feed/workbench | 25 passed |
| `pytest --collect-only -q` | 835 collected, no collection errors |
| `scripts/schema-runtime-smoke.sh` | pass; migration/API/worker image migration manifest hashes match |
| `make PYTHON=.venv/bin/python lint typecheck` | pass; Ruff and mypy |
| Frontend typecheck and lint | pass |
| Python `compileall`, `git diff --check` | pass |

The fresh schema test exercises idempotent replay and payload conflict, cross-account DB/API rejection, immutable requirements, illegal transitions, concurrent row-version CAS, queued coordinator uniqueness, distinct-Work admission, Run owner/input immutability, Run success distinct from Work completion, revision/epoch fencing, pause/stop convergence, and actual API/worker role grants.

## Phase 1 exit criteria

| Criterion | Status |
|---|---|
| Accept reminder and Research mandates without Agent through the same command, optionally without Conversation | Met; Work domain and HTTP tests |
| Query and link Work across Conversations without copying authority | Met; domain and HTTP tests |
| Revise, pause, resume, stop with row version, revision, epoch, and convergence | Met; controlled DB/domain/API tests |
| Controlled execution receipts exercise success, completion, cancellation, failure, and stale revision | Met; Work domain tests |
| DB and application reject illegal state and cross-account relations | Met; real PostgreSQL tests |
| Empty schema and runtime-role/migration-image checks pass | Met on isolated empty PostgreSQL; manifest smoke and role tests |

## Deferred and discovered issues

- No true background Work/chat parallelism, Generic Work Agent, Subagent, complete Artifact/Delivery/Budget/UI integration, or Work schedule/reminder execution was added. Reminder admission remains a domain-level queued Run with no Phase 3 executor dispatch. Deleted Research Task-specific schedule is not replaced by a second Research lifecycle.
- The pre-existing local JSON `TaskScheduler`/sandbox reminder tool/worker direct-send handler is still present and can be used separately from Work. The design explicitly assigns its replacement to Phase 3 (section 11, Phase 3); Phase 1 does not claim the old reminder delivery path is unified or reliable. This is not a retained Research Task/Work compatibility layer, but it is an outstanding product-level dual path for reminders until Phase 3.
- Work input attachment references and full Work/Artifact version adoption are later-phase integration. This phase validates current-revision Research report/save evidence for completion without claiming the complete Phase 4 artifact model.
- No live Temporal end-to-end run or full 835-test suite was run; the directly related PostgreSQL, Redis, domain, API, contract, and frontend gates above passed.
- The existing shared Compose database has 53 migrations and nonempty legacy data. The newly built API image correctly fails schema verification because migration 054 is missing there. Migration 054 deliberately rejects nonempty Task/Run data and has no historical backfill. Deploying this branch onto that database needs an explicit data/reset decision outside this Phase 1 implementation; no business data was altered here.

Review state: Phase 1 exit criteria met on a fresh target schema. Ready for code review and a scoped commit after approval; not deployed, and no commit was created.
