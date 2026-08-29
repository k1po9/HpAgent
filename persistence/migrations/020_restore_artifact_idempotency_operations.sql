-- Preserve the artifact operations introduced by 010 when extending the
-- idempotency operation allowlist for file workspace commands.
SET search_path TO hpagent, public;

ALTER TABLE idempotency_commands
  DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands
  ADD CONSTRAINT ck_idempotency_commands__operation CHECK(operation IN (
    'create_conversation','send_message','cancel_run','retry_run',
    'bind_identity','revoke_identity','create_artifact','create_artifact_version',
    'create_upload','delete_file'
  ));
