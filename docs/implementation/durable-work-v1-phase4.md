# Durable Work V1 — Phase 4

Architecture and scope follow only `docs/design/durable-work-v1-implementation-design.md`, particularly §9 and the Phase 4 A–D boundary. Phase 5 delegation remains disabled.

## Product and execution boundary

Work inputs are explicit account-owned file references with a purpose and optional source Message. Conversation links neither import attachments nor grant Workspace access. Admission freezes these references; current grants and input availability are checked again before a model dispatch after capacity waiting. Revocation cancels affected execution. File deletion/retention includes Work references and Artifact versions.

Artifact versions record their producing Run, root Execution and registered operation. Research uses the common publisher without a fake Message or a Research-specific Artifact owner. Generated results and revision-specific adoption are separate append-only records. Artifact viewing from a Work opens the exact referenced version. An asynchronous HTML request creates an Artifact-build Work; its finite Run uses the common lifecycle and owns model cost. A new Run retry produces a new version, preserving previous provenance. Editing an existing standalone Artifact requires no source Message.

Reusing an existing completed version requires an explicit current-revision input/evidence reference selected before execution. Admission freezes the exact version and role in the Run manifest. A new evaluation can adopt that version for its own revision without changing the original producer; a historical reference alone cannot satisfy a new revision.

Required Workspace saves retain their frozen Work/revision, target and expected file revision. Report readiness, save commitment, user acceptance and channel acceptance are distinct evidence. Ordinary reports can complete before notifications are delivered. Requirements that include user confirmation or channel acceptance wait for the corresponding persisted receipt; receiving a channel receipt alone never fabricates user acceptance.

## Governance

Run budget reserve/settle/release now includes the cumulative Work ledger in the same transaction. Model coordination locks Account/day, then Work budget, then Run budget. Terminal/cancelled attempts can settle previously reserved usage, once; reserve cannot start new work after authority is revoked. Provider calls with an unknown outcome consume estimated usage. Requests cancelled before dispatch release their reservation. New revisions, Runs and model fallback attempts cannot reset a Work's limits.

Budget increases are account-owned, idempotent commands with both Work and budget versions and an audit event. Raising an exhausted budget creates a durable wakeup. Finite configurable admission limits and PostgreSQL capacity tickets bound coordinator, model, tool and fetch concurrency across processes. Queue arbitration rotates eligible accounts. Global and per-account capacity leave room for interactive calls; waiting does not occupy a physical ticket. Held tickets renew and recover by lease expiry.

## Notifications and recovery

Notifications hold immutable business payloads and producing event/operation references. Targets are selected separately from Conversation links, have immutable versions and recheck current verified identity bindings. Work target selection records its command. `current_channel` is resolved at acceptance to an actual verified QQ target using the supplied source Message; the unresolved marker is never stored in a requirement. Group targets accept summaries only.

Each notification/target pair has independent delivery state, part progress and adapter receipt. Web acceptance means commitment to the private account inbox; QQ acceptance means the adapter accepted the send. Neither is a user-read receipt. Partial receipt information survives sender loss. An expired sending lease becomes uncertain and is not automatically resent. Versioned resolution commands record an explicit accepted/not-sent decision or acceptance of duplicate risk. Obsolete fulfillment cannot be resent by that decision.

Chat delivery references its completed source Message instead of copying the full answer into notification payloads. Delivery claims enforce configurable global/account capacity and account rotation independently of the producing Run's state.

Pause/stop/revise cancel unsent fulfillment and obsolete result pushes while preserving control-event notifications. In-flight uncertainty keeps pause/stop from claiming convergence. Late receipts preserve delivery facts without satisfying another revision/epoch. Resuming a one-shot reminder first revalidates a prior accepted receipt for the same revision/content/target version, preserving its original delivery reference instead of sending again. Daily occurrences remain separate.

Work events have their own PostgreSQL sequence, outbox and authenticated recovery stream. Redis is a projection and cannot change business state. Outbox publication failure is retryable. Work control in QQ uses the verified ingress boundary and creates no chat/model Run. The UI maintains independent Work state, event cursors, account reset and version guards, shows budgets, exact results, Workspace saves and channel receipts, and offers control, explicit budget increases and delivery decisions. Live connections are bounded; the complete visible list also recovers from PostgreSQL snapshots.

## Final schema relationships

Migration `057_work_integration.sql` follows the existing checksum runner. Like the earlier Durable Work development phases, it requires an empty development Work/Artifact store and provides no historical backfill. Earlier migration files are unchanged.

| Relation | Ownership and invariant |
|---|---|
| `work_input_refs` | Account/Work/File and optional source Message FKs; explicit purpose and revocation |
| `artifact_versions` | Account/Run/Execution/operation and optional File FKs; immutable producing provenance and completed result |
| `work_artifacts` | Exact version, source revision, role and optional accepted revision/event; append-only |
| `work_budgets`, `work_usage_ledger` | One cumulative budget per Account/Work; operation/Run/dimension settlement |
| `capacity_queue`, `capacity_turns` | Cross-process tickets, leases and account rotation |
| `delivery_targets` | Account/Work, channel, verified binding subject, audience/content scope, immutable version and selecting command |
| `notifications` | Immutable business key and bounded payload; producing Work/revision/Run/Execution/operation or Work event |
| `deliveries`, `delivery_decisions` | Account/notification/target FKs, accepted receipt and part progress; append-only explicit resolution decisions |
| `work_result_acceptances` | Account/Work/revision/Run/exact Artifact version/command FKs; append-only user evidence |
| `trace_runs`, `model_input_snapshots`, `model_dispatches` | Work/revision/root Execution correlation, immutable dispatch marker; entitlement-scoped visibility |

The API and worker roles receive explicit grants for their command, publisher and delivery paths. Account consolidation rejects accounts with Work/notification resources. Separate Artifact Workflow/outbox dispatch and the `qq_deliveries`/`reminder_intents` models are removed. The operations viewer and Temporal registry checks use the unified runtime.

## Verification scope

A disposable PostgreSQL database was initialized through the complete migration chain, using the real API and worker roles. Focused checks exercise concurrent cumulative reservation and terminal settlement, receipt-gated reminders, stop/uncertainty resolution, revision isolation, Artifact producing provenance and retry, combined user/channel acceptance, resumed one-shot receipt revalidation, capacity lease recovery, explicit input revocation, budget adjustment, the QQ adapter/control boundary and the existing Research publication path. Existing contract/generator/composition checks and frontend type/store/feed checks were used where affected.

This is focused implementation verification, not the design's complete real Temporal/live QQ/browser/SIGKILL acceptance campaign. Existing application databases were not reset or migrated during this work.
