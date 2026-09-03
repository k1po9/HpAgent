# File Assistant F4 HITL Acceptance

## Scope

F4 introduces the durable approval boundary required before future high-risk file
actions such as overwriting an external destination, deleting external content, or
sending a file. It does not classify the existing derived-output tools as destructive:
those tools continue to publish a new immutable output and remain
`idempotent_write`.

## Runtime map

1. A Worker calls `FileActionApprovalService.request` with the owned account,
   conversation, run, operation, tool name, human-readable summary, and SHA-256 of
   the canonical arguments.
2. PostgreSQL stores only compact intent metadata in `file_action_approvals`; raw
   arguments and file content are not stored in the approval row or Temporal History.
3. The authenticated account lists pending requests with
   `GET /api/v1/runs/{run_id}/file-action-approvals`.
4. The account approves or rejects using the CSRF-protected endpoints. Both commands
   require `Idempotency-Key` and use the existing `idempotency_commands` table.
5. Before entering the external side-effect window, the Worker calls `consume` with
   the exact run, operation, tool, and arguments hash. An approved grant is consumed
   atomically and can never authorize a second execution.
6. Operation Intent, fencing, reconciliation, and uncertain-side-effect handling
   remain authoritative after the approval gate.

## Migration

`029_file_action_approvals.sql` creates the ownership-scoped approval state machine,
indexes pending expiry and owned-run queries, grants API/Worker least-privilege access,
and adds `approve_file_action` / `reject_file_action` to command idempotency.

The migration was applied through the normal migration runner. Its recorded checksum
must not be edited; subsequent changes require migration 030 or later.

## Safety invariants

- A grant is bound to one `(run_id, operation_id, tool_name, arguments_hash)` intent.
- Only the owning account can see or decide a request.
- Only `approved` and unexpired grants can be consumed.
- Consumption is atomic and single-use.
- Approval DTOs do not expose the arguments hash.
- Existing create/transform/version tools do not require approval because they create
  immutable derived outputs rather than overwriting inputs or external destinations.

## Deferred integration

No external overwrite, delete, or send tool exists in the current production Tool
Registry. Therefore F4 intentionally does not add a pretend destructive tool or pause
ordinary runs. When the first such adapter is introduced, its Activity must request
and await approval, consume the grant immediately before recording/invoking the
non-idempotent side effect, and then continue through the existing Operation Intent,
fencing, and reconciliation path. A Temporal signal/outbox wake-up should be added at
that point so the workflow can remain suspended instead of terminating while waiting.

## Verification commands

```bash
TMPDIR=/tmp ./.venv/bin/python -m pytest -q \
  test/test_file_action_approval_contract.py

APP_DATABASE_URL='postgresql://hpagent_api:***@127.0.0.1:5434/hpagent' \
WORKER_DATABASE_URL='postgresql://hpagent_worker:***@127.0.0.1:5434/hpagent' \
MIGRATION_DATABASE_URL='postgresql://hpagent_migrate:***@127.0.0.1:5434/hpagent' \
TMPDIR=/tmp ./.venv/bin/python -m pytest -q \
  test/web_persistence/test_file_action_approvals.py
```

Expected focused result: `4 passed`.
