# Phase 3 W3-C · Delete Old State Authority

Baseline: `2ca53e1676d9930778b42601bdfcb3edc5257b8e` (W3-B complete).

## Deleted authorities and storage

W3-C removed the inactive QQ/legacy conversation-state chain:

- `session.SessionStore`, including Redis session lists, process-memory fallback, WAL replay,
  checkpoint files, session archive state and Hindsight calls routed through SessionStore;
- `WorkspaceDB` and its SQLite `users`/`sessions` metadata authority;
- legacy Session models and filesystem session/history helpers;
- `TurnMemoryService` and `SessionArchiveService`, which had no production caller;
- old WAL/history session viewer and old JSONL-to-Hindsight migration script;
- `ActionRuntime.session_store` event writes and dead worker composition/config fields.

The read-only execution observatory now reads Web and QQ Runs from canonical PostgreSQL state and
uses committed Message origin to label the surface. It no longer treats WAL/history files as a
durable QQ state source.

## Authority map after W3-C

| State | Authority | Retained owner |
|---|---|---|
| Conversation / Message / Session | PostgreSQL | `conversation_domain`, `persistence` |
| Run lifecycle / execution | PostgreSQL + Temporal | `agent_activities`, `agent_workflows`, `orchestration` |
| QQ delivery | PostgreSQL `qq_deliveries` | `application.qq_delivery`, `conversation_domain.delivery` |
| Online events / cache / ambient group context | optional Redis | `storage.redis`, `web_domain.run_events`, `memory.group_context` |
| Long-term memory | Hindsight | `memory.hindsight_client`, `application.memory_retention` |
| Workspace-specific state | Workspace files, Git and isolation | `workspace`, `sandbox.git_repo` |
| File / Document / Artifact state | PostgreSQL metadata + tenant/workspace objects | file/document/artifact capabilities |

Workspace file isolation, Git recovery, Run file workspace and tenant object storage remain. They
do not own Conversation or Session state.

## Canonical behavior proof

- A new QQ service instance replays the same provider message from PostgreSQL receipts and reuses
  the PostgreSQL active Session without SessionStore/WAL recovery.
- Duplicate/redelivery creates one Run, one Session and one start-run Outbox event.
- Web, QQ private and QQ group Conversations remain isolated while using the same command service.
- Concurrent distinct first deliveries for one route now converge on the deterministic PostgreSQL
  Conversation. The insert accepts either equivalent unique constraint as the already-created row,
  closing the W3-B race on `uq_conversations__account_conversation`.
- Outbox restart plus Workflow/Activity worker-kill recovery remain green.

## Validation

| Check | Result | Evidence |
|---|---|---|
| Focused authority/composition/runtime contracts | **106 passed** | `W3_C_focused_validation.txt` |
| PG persistence, redelivery, isolation and workspace integration | **39 passed** | `W3_C_persistence_validation.txt` |
| Outbox + Workflow/Activity worker restart/recovery | **10 passed** | `W3_C_recovery_validation.txt` |
| Repository collection | **692 collected** | `W3_C_collection_validation.txt` |
| Authority/reachability + production registry | **PASS** | `W3_C_reachability_validation.txt`, `W3_C_authority_scan.json` |

The scan reports zero retired modules, paths, imports, definitions and composition/config fields;
all retained state systems and canonical Temporal registrations are present. Final Ruff, diff and
phase2_2 integrity results are recorded in `W3_C_static_validation.txt`.

The user's pre-existing phase2_1 truth-table edit remains excluded. Frozen phase2_2 evidence is
unchanged.
