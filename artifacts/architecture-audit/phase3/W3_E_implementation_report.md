# W3-E — Repository Closure Validation

## Judgment

**G05 PASS / W3 CLOSED for the audited HEAD plus the W3-E working-tree fix.**
The repository's retirement/registry checks and all 54 selected integration tests pass.
The initial network-blocked judgment is superseded by the 2026-09-17 proxy follow-up:
all four repository images build, all four pass `pip check`, a fresh migration image applies
all 36 migrations to an empty PostgreSQL database, API readiness passes, and real production
entrypoints register pollers on all four required Temporal queues. No W4 implementation is included.
Existing application images/services were not substituted for this fresh-image evidence.

## Audited state and scope

- Branch: `feat/hpagent-web`; HEAD: `314a03dcbc0db460292941c9d2f6ad8c5847519d`.
- Entry worktree: only `artifacts/architecture-audit/phase2_1/03_architecture_truth_table.md`
  was modified. That user edit was preserved, as were ignored local configuration/data.
- Final reviewed delta: four obsolete `AgentConfig` fields removed, one existing regression
  extended, and W3-E evidence added. No commit, deployment or existing application DB reset.
- Phase 2.2 source of truth: `03_delivery_sequence_and_gates.md` (W3/G03/G04/G05/G11),
  `08_retirement_plan.md`, `retirement_candidates.csv`. W3-A/B/C/D implementation reports
  were read as claims to verify, not accepted as current test results. No broad historical reread.
- Current source, direct caller inspection, fresh-process import tests and actual production
  registry capture were used. Prior W3 artifact files were not regenerated/overwritten.

## Retirement checks

| Candidates / category | Current evidence and result |
| --- | --- |
| R01–R02 legacy Web Workflow/Activity | Old modules absent. `orchestration/web_dispatcher.py:TemporalClientAdapter.start_web_run` starts only `AgentLifecycleWorkflow`; AlreadyStarted recovery accepts only that type. Lifecycle uses canonical `run_lifecycle_activities` and `agent_workflows`. PASS. |
| R03–R06 old loop, Facade and Hosts | `agent_execution` absent; production `orchestration/worker.py` constructs `DurableAgentActivities`, `AgentDataStore`, `ChatRunInputLoader`, shared Brain/Actions and segmented Activities. No old import/definition or reachable runtime. PASS. |
| R07–R08 QQ long-session Workflow / harness turn | `orchestration.workflow` and `harness` absent; lazy old export absent. QQ `application/ingress.py` → `application/conversation.py` → `SurfaceConversationCommands` → `CommandService` commits PG Run/Outbox; no turn Workflow. Reflection/Metrics and reminder handler remain. PASS. |
| R09 old state authority | `session`, WorkspaceDB, old archive/turn memory services and operational WAL scripts absent. PG owns Conversation/Message/Session/Run and QQ delivery; no SessionStore/WAL/checkpoint or mailbox admission fallback. PASS. |
| R10, R15 experimental Agent/config | `agent` package and `config/agents.yaml` absent; no MultiAgentConfig/AgentEntry exports or registered experiments. Tracked configuration has no migration flags. Ignored local config exception described below. PASS for canonical runtime. |
| R11–R13 JSON identity | Old account models/service and merge script absent. `account/__init__.py` exposes PG service and backend validator; QQ resolves PG identity and Web credentials use PG. No JSON fallback. PASS. |
| R14 dispatch/flags | No durable enable flag, old Web enable/gate variables, dual runtime registration, credential JSON environment fallback or standalone `web_worker`. Research/Artifact dispatch is independent retained product capability, not a legacy Agent fallback. PASS. |
| R16 schema/install | Migration runner and ordered SQL assets preserved, including functions, constraints and grants. Disposable PostgreSQL integration invokes the real migration runner on a new database. All four image builds and isolated production entrypoint startup pass after configuring the existing proxy. Clean-image/schema acceptance PASS. |
| Extracted survivors | `actions.contracts`, `brain.contracts`, `application.execution_contracts`, `application.context_builder`, `application.prompts`, `application.scheduler`, `memory.activities/workflows/maintenance`, `orchestration.run_lifecycle_contracts` remain canonical owners. Fresh-process import guard and ownership tests verify consumers without loading retired owners. PASS. |

`W3_E_authority_scan.json` records current source SHA-256, zero retired modules/paths/imports/
definitions/config-composition fields, retained owners, and actual production registry capture:

| Queue | Workflows | Activities |
| --- | ---: | ---: |
| hpagent-web-lifecycle | 5 | 22 |
| hpagent-web-agent | 5 | 11 |
| hpagent-document | 0 | 1 |
| hpagent-task-queue | 2 | 3 |

Research, Document, Artifact, Reflection and Metrics registrations are retained. Web HTTP send,
cancel and retry call `CommandService`; QQ shares its transaction logic and PG admission lock.
Outbox Start/Cancel uses deterministic workflow identity; tool approval signals canonical
ToolExecutionWorkflow. Segment wait/resume reacquires execution authority with new fencing tokens.
QQ delivery reads committed PG output separately from Agent execution.

### Remaining references and boundaries

- No executable production reference to a retired implementation was found. Names in historical
  architecture artifacts, trace event labels and comments do not create runtime reachability.
- Ignored personal `config/config.yaml` contains `agent.mode`, commented multi-agent configuration,
  old timeouts and `agents.yaml` references. The dataclass loader warns on unknown keys and ignores
  them; no removed field can enable a retired runtime. File preserved, not counted as a clean
  tracked config example. No `.env` secrets were read or included in evidence.
- Redis cache/group context/events, Hindsight memory, PG persistence, workspace/Git/tenant storage,
  context, reflection and scheduled maintenance remain intentional owners. SQLite in crash-test
  benchmark harnesses models side-effect records, not product QQ Session authority.
- `WEB_FAKE_EXECUTOR_ENABLED` is a development test facility explicitly rejected in production by
  settings validation; it is not a durable migration flag or a retained old runtime.
- Model/provider and browser fallbacks, tool side-effect classification normalization and terminal
  event reconciliation remain current capability behavior, not dual orchestration.
- Migration checksum backfill is inside the retained schema runner; rewriting migration history
  or baselining schema is outside this review. No alternate Run/Session authority is introduced.

## Minimal closure fix

Removed `AgentConfig.event_fetch_limit`, `activity_timeout`, `archive_timeout`, and
`idle_timeout_minutes`. Targeted repository search found only their definitions (plus ignored local
config and historical evidence), with no surviving reader. They exposed the deleted event fetch,
whole-turn Activity/archive and old Session idle controls. The existing state-authority regression
now rejects all four fields. No business behavior or Temporal timeout was changed.

## Validation actually executed

| Check | Result / evidence |
| --- | --- |
| Focused ownership/runtime/QQ/replay/resources/config tests | **115 passed**, one Starlette/httpx deprecation warning; `W3_E_focused_validation.txt`. Before the four-field fix. |
| Changed state-authority plus Web config regression | **17 passed**, same warning; `W3_E_fix_validation.txt`. After fix; overlaps first run. |
| Repository collection after fix | **686 tests collected**; `W3_E_collection_validation.txt`. Collection is not full-suite execution. |
| Current authority/registry scan | PASS; `W3_E_authority_scan.json`. |
| Tracked compatibility/import/export/config scan; clean default config; requirements consistency | PASS; `W3_E_compatibility_validation.txt`. Initial probe incorrectly called WebApiSettings without required arguments; corrected to from_env in an empty environment with a dummy DB URL. No product defect. |
| Ruff on two changed Python files, diff whitespace, frozen Phase 2.2 diff, Compose config | PASS; `W3_E_static_validation.txt`. |
| Isolated PG/Redis/Temporal integration | **54 passed, no skips**, 395.95 seconds; `W3_E_integration_validation.txt`. |
| API image | PASS; `W3_E_api_proxy_final_build.txt`. Initial network failures retained in earlier build logs. |
| Migration image | PASS; `W3_E_migrate_proxy_final_build.txt`. |
| Worker image | PASS, including Chromium installation; `W3_E_worker_proxy_final_build.txt`. |
| Document image | PASS; `W3_E_document_proxy_final_build.txt`; entrypoint/Docling imports and dependency check also pass. |
| Fresh-image installation/startup | PASS; `W3_E_install_validation.txt`, `W3_E_image_inventory.txt`, and final container logs. Four pip checks, 36 migrations, API readiness, seven workflow/activity poller checks across four queues. |

Builds used unchanged repository Dockerfiles and their intended root/src contexts with isolated
W3-E tags. Normal Docker layer/pip caches were used; this is a clean-environment image build/start
check, not a claim that every dependency byte was downloaded without cache. Image IDs are recorded
in `W3_E_image_inventory.txt`. No requirements, Dockerfiles or dependency mirrors were changed.

### Proxy follow-up and installation evidence (2026-09-17)

The user identified `proxy_all_on`. Inspection of `/root/.bashrc` located its shell/Docker helpers
and the Windows gateway proxy at `http://172.24.192.1:19674`. Docker daemon already used that proxy;
no Docker restart or host configuration rewrite was needed. Explicit upper/lowercase HTTP/HTTPS
proxy build arguments supplied the shell, pip and APT paths. `W3_E_proxy_validation.txt` records
connectivity checks. Transient TLS EOF/read timeouts and APT 502 failures remain in earlier logs;
successful cached retries supersede them. The initial pip empty-index error was network-related,
not proof of an invalid aiohttp requirement.

The reproducible `W3_E_install_probe.py` creates an isolated Docker network, disposable PostgreSQL,
Redis and a separate Temporal server. It runs the four newly built images, checks dependency
consistency, runs the migration image against an empty database, compares all 36 recorded versions
to repository SQL assets, and starts the actual API, Worker and Document entrypoints. A minimal
fixture configuration disables external channels, Hindsight, MCP and tool RAG; it configures an
unused model endpoint because this probe validates startup/registration, not model inference.
Canonical business execution is covered by the separately recorded 54 integration tests.

Final live Temporal verification found one poller for each of:
`hpagent-web-lifecycle` Workflow/Activity, `hpagent-web-agent` Workflow/Activity,
`hpagent-task-queue` Workflow/Activity, and `hpagent-document` Activity. API `/health/ready`
returned ready. Temporary containers and their disposable volumes/network were removed; existing
application services and data were untouched.

The first startup probe omitted Document's required file-store mount and therefore timed out on
its queue; the Document log reported `file store root is unavailable`. Compose already declares
that read-only mount. The probe was corrected to provide an empty read-only fixture directory,
then passed in a newly created environment. The initial output/container logs are retained with
`_initial` suffixes. This was a probe setup error and required no product code change.

Exact reproducible validation invocations are in `W3_E_validation_commands.txt`. Integration uses
a disposable PG container, disposable Redis container and isolated Temporal namespace. Production
PG/Redis data is not touched; model/QQ/Hindsight network effects are controlled test doubles.
The disposable fixture applies `persistence.migrate.migrate()` before persistence tests. An additional
read-only migration-table probe was attempted after the suite completed, but its container had
already been automatically removed; it produced no extra schema evidence. Fresh-schema evidence
is the successful real migration fixture and subsequent PG tests, not that post-run probe.

## DEFER_TO_W4

1. `bootstrap/qq.py:build_qq_runtime` still constructs shared Brain/Action capabilities with broad
   `Any` arguments, which `orchestration/worker.py` consumes for both surfaces. Move ownership and
   tighten protocols in W4; this constructs only canonical objects today.
2. `actions/runtime.py` retains optional execution IDs/session-scoped cache behavior and stale Host
   commentary. Canonical `agent_activities/runtime.py` passes `request.run_id` to reset/select/execute
   (lines 497/509/906 at audit). There is no old Host caller; signature/adapter cleanup is W4.
3. `application/context_builder.py` retains channel/profile adaptation terminology. Current context
   consumers use it; no retired implementation is imported. Protocol cleanup stays in W4.

These are not G05 runtime blockers and were left unchanged. No W3 closure blocker remains in
the audited tree after the four-field fix and successful proxy-assisted image/install validation.
**G05 PASS / W3 CLOSED. Stop after W3-E; do not enter W4.**
