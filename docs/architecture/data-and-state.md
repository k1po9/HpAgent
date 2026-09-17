# Data and State Ownership

## PostgreSQL

Application PostgreSQL is authoritative for accounts, identity bindings, conversations, messages, sessions, Runs, Outbox events, workflow-execution facts, transcripts, operations, leases, fencing tokens, delivery state, traces, files, research records, and artifacts. State transitions that must agree are committed in one transaction.

Important ownership groups:

- **Identity and conversation:** `accounts`, identity bindings, conversations, messages, sessions.
- **Execution:** Runs, workflow executions, Outbox events, agent transcripts, operations, waits, execution segments, leases, and fencing tokens.
- **Delivery and trace:** QQ deliveries, trace runs, and trace events.
- **Files and workspace metadata:** stored files, message/run bindings, persistent revisions, approvals, budgets, and usage ledger.
- **Research and artifacts:** tasks, plans, sources, evidence, reports, artifacts, and artifact versions.
- **Heavy documents:** normalized-document operation results and status.

## Redis

Redis owns transient coordination and cache data such as short-lived session/event context, notifications, and tool/runtime caches. Losing Redis may degrade in-flight convenience state, but it does not replace committed PostgreSQL business state.

## Hindsight

Hindsight owns long-term semantic memory banks and retrieval indexes. HpAgent stores stable account and Run correlation metadata around memory operations; it does not duplicate the semantic index in application PostgreSQL.

## Git and filesystem storage

Account-scoped Git workspaces own editable project history and working files. File-store volumes own uploaded and generated blobs; PostgreSQL owns their metadata, lineage, permissions, and Run associations. Temporary Run and document directories are execution material, not business authority.

PostgreSQL, Redis, Hindsight, and Git therefore own different categories of state. They are cooperating stores, not duplicate authorities.
