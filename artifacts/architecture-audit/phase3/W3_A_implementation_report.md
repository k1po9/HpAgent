# Phase 3 W3-A · Extract Survivors

## Scope / baseline

Baseline and pre-edit HEAD: `630ce8de2f1f72a1663a95a8f8ffa3db622d8fff` (W2 closed). This report concerns survivor extraction, not W3-B deletion or W4 composition cleanup. The implementation commit is resolvable with `git log -1 --format=%H -- artifacts/architecture-audit/phase3/W3_A_implementation_report.md`; no self-referential commit hash is fabricated.

Current source was read directly. The frozen phase2_2 candidate CSV was not used as the reachability input and was not modified. The user's existing phase2_1 truth-table edit is excluded from this commit. No runtime file/package, migration, retained fixture, or legacy test is deleted. Forwarding imports in original files are temporary W3-B retirement surfaces, not permanent compatibility packages.

## Extracted survivors and actual consumers

| Former owner / symbols | New capability owner | Actual canonical callers / reason |
| --- | --- | --- |
| `agent.protocol.ActionRequest`, `ActionResult` | `actions/contracts.py` | `actions.runtime`, `brain.engine`, `agent_activities.runtime`; importing the action contract no longer initializes the experimental agent package |
| `agent.protocol.BrainDecision` | `brain/contracts.py` | `brain.engine` model-step result; canonical source-plane/E2E doubles use this same type |
| `agent_execution.facade.ExecutionRequest`, `ExecutionContextProvider`, `StableExecutionFailure`, `EventSink` | `application/execution_contracts.py` | Chat request/context adapter, durable Activity failure classification, Trace sink port; legacy Facade now imports the surviving types |
| `agent_execution.web_adapters.PostgresWebRequestLoader`, `WebExecutionContextProvider` | `application/chat_execution.py` | `compose_web_workers` injects PG context loader into `DurableAgentActivities`; shared Web/QQ Chat capability |
| `agent_execution.chat_bindings.ChatExecutionBindings` | `conversation_domain/execution_bindings.py` | Durable execution session/transcript identity bindings injected by worker |
| `agent_execution.chat_run_input.ChatRunInputLoader` | `conversation_domain/run_input.py` | Lifecycle source adapter builds canonical `AgentRunInput` from PG identity/origin |
| `agent_execution.activity_control.TemporalActivityControl` | `agent_activities/control.py` | Durable tool Activity cancellation/deadline/heartbeat implementation |
| `agent_execution.run_budget` service, DTO, dimensions, errors | `resources/run_budget.py` | Agent, Document, Research resource admission/settlement; shared PG budget implementation |
| `agent_execution.model_budget_context` scope/identity helpers | `resources/model_budget_context.py` | `resources.resource_pool`, Agent and Research Activities; model-attempt budget attribution |
| `agent_execution.tracing` contracts, metadata utilities, context, repository, sinks, observer | `tracing/` | Agent/Document/Research Activities, lifecycle completion observer, Web API trace queries; independent observability capability, with no import back to retired execution |
| `agent_execution.web_events` event projection, phases, publisher protocol | `web_domain/run_events.py` | Worker event factory, Trace projection and API fake executor; wire names/sequence semantics unchanged |
| `harness.context_builder.HarnessContextBuilder`, `harness.prompts.PromptLoader` and default identity | `application/context_builder.py`, `application/prompts.py` | `ContextAssemblyService` and worker injection; class names unchanged, no package cosmetic rename |
| `harness.activities` reflection/metrics injection and three Activities | `memory/activities.py` | Production scheduled Worker registry; long-session `process_turn`/archive Activities stay in harness |
| `orchestration.workflow.ReflectWorkflow`, `MetricsReportWorkflow` | `memory/workflows.py` | Production Worker and Temporal Schedule setup; workflow/activity type strings and retry/timeouts unchanged |
| `bootstrap.qq.HindsightMaintenance` | `memory/maintenance.py` | Shared reflection/metrics services; QQ composition consumes the memory adapter, no SessionStore needed |
| `orchestration.scheduler.ScheduledTask`, `TaskScheduler` | `application/scheduler.py` | Production `user_reminder` handler/poll loop and sandbox reminder tool factory; persistence format and account filtering unchanged |

Only imports/ownership changed in canonical execution. Existing PG context, memory recall/retain, event/trace IDs, queues, Workflow names, retry policy, schemas and reminder delivery behavior are preserved. Ruff also corrected existing F/I issues in changed files, including the reminder's missing type-only scheduler import.

## Rechecked survivors that must not be mechanically moved/deleted

- **ExecutionResult / old ExecutionControl / LifecycleWebReplySink**: actual callers are retained Host/loop implementations and their tests. They remain in `agent_execution.facade` / `web_adapters`; they are not promoted into canonical contracts merely because older evidence listed them.
- **ExecutionAuditSink/Factory, NullExecutionAuditSink, BrainActionLoop and logging audit**: legacy-only callers remain. No new canonical audit ownership is invented.
- **Queue constants / FailureInput / RunLifecycleInput / RunAuthority**: already owned by `orchestration.run_lifecycle_contracts`, consumed by canonical lifecycle, Research and Document routing. Retain that module; no blanket orchestration retirement or queue rename.
- **Common Event/EventType/ChannelType/UnifiedMessage**: already in `common.types`; current context, Actions and channel/reminder callers still use them. Retain existing ownership.
- **Memory retention**: canonical `ContextAssemblyService` and `MemoryRetentionService` use PG messages and Hindsight. `TurnMemoryService` and `SessionArchiveService` have no canonical callers. The archive summary/tag/YAML/history helpers remain in `session.workspace` for retained archival callers. No speculative PG archive flow or second short-term authority was added.
- **QQ Session/storage**: worker still constructs `session.db.WorkspaceDB`; importing it executes `session.__init__`, which imports models, SessionStore and workspace helpers. This is real import reachability, not proof that canonical QQ constructs SessionStore. These modules are conservatively STILL_REACHABLE. Deciding/removing the residual SQLite composition is outside W3-A; a whole-file deletion is prohibited.
- **storage package**: LocalFileStore, TenantFileStore, RedisCache and protocols have canonical workspace/file/QQ context consumers. They remain shared infrastructure, not QQ-only retirement candidates.
- **Old orchestration export**: `orchestration.__getattr__('OrchestrationWorkflow')` still contains a lazy import. Scheduled workflows no longer depend on it. The conservative static map labels `orchestration.workflow` STILL_REACHABLE; fresh-process canonical imports prove the lazy old Workflow is not loaded. W3-B must remove/review the export before deleting its module.
- **Legacy Web / QQ**: WebRunWorkflow, execute_agent_activity, Hosts, Facade/loop and QQ long-session process/archive remain as source. FakeRunExecutor is imported by API composition and gated to non-production; map labels it STILL_REACHABLE rather than equating 'fake' with safe deletion.

## Reproducible retirement map

Run `.venv/bin/python scripts/audit_w3_reachability.py` to rebuild:

- [Current retirement map](W3_A_retirement_map.csv): each scoped module's actual source/test/script import sites, baseline and current import chains, new owner, reason and status.
- [Machine-readable reachability](W3_A_reachability.json): explicit roots, baseline revision, pre-commit HEAD, exact current-source SHA-256, dynamic import inventory and counts.

Roots are Docker/entrypoint `main`, Web API `__main__`/app, standalone Web worker, Document worker and migration entrypoint. The graph resolves absolute/relative imports, parent package initializers, function-local/type imports and literal dynamic imports. It is conservative at module level: a module imported in a lazy export is not falsely treated as active execution. `UNKNOWN` records operational file readers/rewriters that module imports cannot prove unused.

All four requested categories are represented: `baseline_status` records SURVIVOR_EXTRACT_REQUIRED for extracted owners; final `status` reflects the post-extraction tree. No unextracted survivor is silently classified safe. SAFE_TO_DELETE_AFTER_W3_A means **canonical-unreachable retirement candidate**, conditional on W3-B handling listed legacy/test callers and the deletion/install gates; it is not permission to delete each file independently. The graph's zero forbidden capability imports does not hide the separately reported lazy orchestration export or Session reachability.

Manual non-import review: worker's registered workflow list is ReflectWorkflow/MetricsReportWorkflow plus the canonical durable registry; scheduled Activity list comes from memory; `user_reminder` is registered on the extracted scheduler and tool injection still occurs at startup. Channel selection uses explicit provider branches, local tools use statically imported factories, and discovered dynamic imports target standard-library uuid/re. Docker source COPY includes the new capability locations. `scripts/session-viewer.py` reads old WAL/archive files directly and `scripts/merge-account.py` rewrites JSON directly; both stay UNKNOWN for operator-use review. SQL/migrations remain STILL_REACHABLE.

## Validation

Exact reproducible commands are in [validation commands](W3_A_validation_commands.txt). All tests keep original business assertions; test imports/patch targets follow new owners. Added ownership tests reject retired imports in a fresh process, verify scheduled account-failure isolation and unchanged Temporal names, exercise reminder tool creation/persisted reload/account filtering, and check conservative map classification.

The initial integration collection failed because two test imports of BrainDecision were redirected to Actions rather than Brain. They were corrected to `brain.contracts`; [initial output](W3_A_integration_initial.txt) is retained as failed evidence. The first reminder test draft used `message` instead of the existing tool's `content` input; only the test was corrected. An intermediate extraction briefly included legacy-only Result/control/reply symbols; final caller review left them in their original owners and re-ran affected focused tests.

## Gate scope

**W3-A extraction/import gate: PASS.** Full G05 retirement and G11 clean-image/install acceptance remain W3-B/W3 exit work. W2's previous gate evidence is not recounted as new W3 test executions. This phase does not perform mass deletion, composition cleanup, package cosmetic rename, or modify frozen phase2_2 evidence.


## Final results

| Check | Result | Evidence |
| --- | --- | --- |
| Focused unit / contracts / ownership / legacy consumer regression | **178 passed**, 17.25s, no skip/failure | [unit](W3_A_unit_validation.txt) |
| Retained memory/archive/workspace/research schedule capabilities | **29 passed**, 13.87s; overlaps focused run, not added to distinct count | [retained capabilities](W3_A_retained_capability_validation.txt) |
| PG/Redis/Temporal focused integration | **46 passed**, 483.31s, no skip/failure | [integration](W3_A_integration_validation.txt) |
| Fresh-process retired-import guard / ownership tests | PASS, included in 178; separate 4-test check also passed | [ownership](W3_A_ownership_validation.txt) |
| Rebuilt current-source reachability | PASS: zero forbidden capability imports; lazy legacy orchestration export explicitly retained | [reachability](W3_A_reachability.json) |
| Changed Python Ruff F/I | **88 files PASS** | [static](W3_A_static_validation.txt) |
| Working/staged `git diff --check`, baseline ancestry | PASS | [static](W3_A_static_validation.txt) |
| Frozen phase2_2 / user phase2_1 / deleted files | unchanged / excluded / none | [static](W3_A_static_validation.txt) |

Final map has **86 rows**: **46 SAFE_TO_DELETE_AFTER_W3_A**, **38 STILL_REACHABLE**, **2 UNKNOWN**. Its baseline classification records **23 SURVIVOR_EXTRACT_REQUIRED** owners, all extracted; the final status follows actual remaining import reachability rather than assuming an extracted file can always be deleted. The 38 include canonical modules that are not retirement targets, and residual Session/SQLite/lazy-export dependencies that block blanket deletion. The two UNKNOWNs are the operational session viewer and account merge script.

Integration exercises Web/QQ canonical durable completion/cancellation/replay and delivery, QQ ingress/dedup/group memory, PG context assembly/retain, Trace persistence, budgets, non-chat source contracts and SSE. It uses isolated real PG/Redis/Temporal fixtures; external model/QQ/Hindsight calls remain controlled test doubles. The only integration warning is the existing Starlette/httpx deprecation. No full-image build, full W1 crash matrix or G05 deletion gate is claimed.

**W3-A COMPLETE / extraction gate PASS. G05 full retirement and G11 full install/build remain NOT RUN. Stop here; W3-B requires the next user instruction.**
