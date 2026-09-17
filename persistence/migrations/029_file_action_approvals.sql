-- File Assistant F4: durable, ownership-scoped approval grants for destructive actions.
SET search_path TO hpagent, public;

CREATE TABLE file_action_approvals (
  approval_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  run_id uuid NOT NULL,
  operation_id varchar(255) NOT NULL,
  tool_name varchar(200) NOT NULL,
  action_summary varchar(500) NOT NULL,
  arguments_hash char(64) NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  requested_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  decided_at timestamptz,
  decided_by_account_id uuid,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_file_action_approvals__operation UNIQUE(run_id, operation_id),
  CONSTRAINT fk_file_action_approvals__run FOREIGN KEY(account_id,conversation_id,run_id)
    REFERENCES runs(account_id,conversation_id,run_id) ON DELETE CASCADE,
  CONSTRAINT fk_file_action_approvals__decider FOREIGN KEY(decided_by_account_id)
    REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT ck_file_action_approvals__status
    CHECK(status IN ('pending','approved','rejected','expired','consumed')),
  CONSTRAINT ck_file_action_approvals__hash CHECK(arguments_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_file_action_approvals__expiry CHECK(expires_at > requested_at),
  CONSTRAINT ck_file_action_approvals__decision CHECK(
    (status='pending' AND decided_at IS NULL AND decided_by_account_id IS NULL AND consumed_at IS NULL)
    OR (status IN ('approved','rejected') AND decided_at IS NOT NULL
        AND decided_by_account_id=account_id AND consumed_at IS NULL)
    OR (status='expired' AND consumed_at IS NULL)
    OR (status='consumed' AND decided_at IS NOT NULL
        AND decided_by_account_id=account_id AND consumed_at IS NOT NULL)
  )
);

CREATE INDEX ix_file_action_approvals__owned_run
  ON file_action_approvals(account_id,run_id,requested_at,approval_id);
CREATE INDEX ix_file_action_approvals__pending_expiry
  ON file_action_approvals(expires_at,approval_id) WHERE status='pending';

ALTER TABLE idempotency_commands DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands ADD CONSTRAINT ck_idempotency_commands__operation
  CHECK(operation IN ('create_conversation','send_message','cancel_run','retry_run',
    'bind_identity','revoke_identity','create_artifact','create_artifact_version',
    'create_upload','delete_file','create_task','trigger_task','update_task_schedule',
    'approve_file_action','reject_file_action'));

GRANT SELECT,UPDATE ON file_action_approvals TO hpagent_api;
GRANT SELECT,INSERT,UPDATE ON file_action_approvals TO hpagent_worker;
