-- Add governance to the existing command boundary, preserving all prior commands.
ALTER TABLE idempotency_commands DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands ADD CONSTRAINT ck_idempotency_commands__operation
 CHECK(operation IN ('create_conversation','send_message','cancel_run','retry_run',
 'bind_identity','revoke_identity','create_artifact','create_artifact_version',
 'create_upload','create_workspace_upload','delete_file','approve_file_action','reject_file_action',
 'accept_work','revise_work','pause_work','resume_work','stop_work','link_work','advance_work',
 'accept_work_result','memory_action'));
