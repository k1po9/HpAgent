# Durable Work V1 — Phase 3

Implementation boundary: `docs/design/durable-work-v1-implementation-design.md`, especially §8 and Phase 3 in §11. This builds on commits `1217d2b` and `189125b`.

## Execution paths

`WorkCommandService` validates a structured immutable requirement. `WorkExecutionPlanner` selects one registered, versioned strategy before admission freezes the requirement, checkpoint and trigger into `runs.input_snapshot`. The root Execution manifest references that snapshot and its fixed strategy. Unsupported capabilities/versions are rejected; there is no fallback to an unrestricted Agent.

All starts use `start_run` and the finite `AgentLifecycleWorkflow` entrypoint. It prepares the common Run lifecycle, loads the frozen strategy, then executes one of:

- `reminder` → deterministic PostgreSQL enqueue, with the same Execution lease, operation attempt and immutable receipt used by other executors. It creates no sandbox, files, Git branch or model request. The Run succeeds with `notification_enqueued`; the Work remains `awaiting_delivery`.
- `research_report` → the existing fixed Research graph within the same Workflow identity. Fixed requirement planning, citation verification, bounded authorized history and required Workspace saves remain intact. History records the producing requirement revision. Completion continues through `WorkCompletionPolicy`.
- `generic_work` → the existing react/plan-and-execute engine using `WorkContextProvider`, without a Conversation or Session. Input consists of the frozen requirement/checkpoint, bounded resource candidates, trigger and Run budget. Typed completion recommendations must reference successful tool receipts belonging to this Run; `WorkCompletionPolicy` makes the acceptance decision. Narrative output is retained as a candidate, with `awaiting_input`, rather than interpreted as a completed mandate. A future continuation commits a durable wakeup.

Main exposes accept/list/get/revise/control/advance/link tools through the same commands as the API. Account, source Message and Conversation are bound by the server. Acceptance keys use the original user Message and a stable mandate slot; committed slot receipts are loaded into retry context. Background Work does not receive Main's management tools.

The JSON reminder scheduler, its tools/configuration, direct channel handler and `start_research_run` adapter are removed. There were no remaining non-user consumers of `TaskScheduler`; memory reflection and metrics retain their existing Temporal workflows and activities.

## Scheduling and schema relationships

The PG due service applies the desired schedule projection at startup and each sweep. There is no Temporal Schedule RPC to recover: `applied_version` acknowledges installation into the PG evaluator. Repeated or competing sweeps acquire Work locks and use stable occurrence keys.

| Relation | Ownership / invariant |
|---|---|
| `work_schedules` | One per Account/Work; requirement FK; desired/applied versions, IANA timezone, next due and evaluation watermark |
| `work_schedule_occurrences` | Unique schedule/version/scheduled UTC instant; Account/Work/schedule and optional wakeup FKs; append-only skipped/superseded facts |
| `work_wakeups` | Existing unique trigger and current-revision admission; one-shot due and future continuation survive process restarts |
| `runs.input_snapshot` | Bounded immutable input; DB validates requirement/checkpoint/trigger and registered strategy against producing Work |
| `run_executions.context_manifest` | Fixed revision, checkpoint version, strategy and input snapshot reference |
| `reminder_intents` | Pending/cancelled enqueue intent; composite producing operation/Run/Execution and Work/revision ownership; cancellation cannot alter its payload |
| `execution_operation_attempts` / `execution_result_receipts` | Existing registered attempt/fence and receipt checks; reminder uses these rather than a separate execution ledger |

Daily recovery coalesces historical missed fires into at most the latest pending occurrence and records `missed_from`; one-shot catches up without dropping the due wakeup. Changes to objective alone retain the schedule version; time or enablement changes invalidate old callbacks. A disabled/old callback records a skipped occurrence and cannot start a Run. User cancellation remains blocked until an explicit control/advance decision.

Once timestamps must contain an offset. Daily times use the requirement's IANA timezone. On a DST overlap the first fold is used once; a nonexistent local time skips that day. Only `catch_up` for once and `latest` for daily are supported. The design's `2026-10-02 09:00 Asia/Shanghai` example resolves to `2026-10-02T01:00:00Z`.

Migration `056_execution_strategy.sql` adds these contracts and explicit API/worker grants without changing earlier migration checksums. Schema validation was performed on a separate empty development database; existing databases were not migrated or cleared.

## Phase boundary and verification

Reminder enqueue currently permits only the account's private inbox reference. It is an intent for Phase 4 delivery integration, not evidence that the inbox or an external channel accepted the reminder. No provider acceptance or user-read receipt is fabricated. Notification targets, channel dispatch, delivery receipts, aggregate Work budgets and the product UI remain Phase 4 work.

Focused checks cover real API/worker PostgreSQL roles, fresh schema application, occurrence replay/coalescing, stale callback suppression, revision timing, Generic fixed context and verified acceptance, durable continuations, Main mandate replay, timezone/DST policy, and one real Temporal reminder through the unified entrypoint without an Agent worker. Existing Work state/fence, context, dispatch, routing and worker cleanup checks were also run. No full Research/QQ/browser/SIGKILL acceptance campaign was run in this phase.
