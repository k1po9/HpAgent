-- Compose Research Tasks with Web conversations and downloadable File outputs.
SET search_path TO hpagent, public;

ALTER TABLE tasks ADD COLUMN conversation_id uuid;
ALTER TABLE tasks ADD CONSTRAINT fk_tasks__conversation
  FOREIGN KEY(account_id,conversation_id)
  REFERENCES conversations(account_id,conversation_id) ON DELETE RESTRICT;

ALTER TABLE runs DROP CONSTRAINT ck_runs__owner_shape;
ALTER TABLE runs ADD CONSTRAINT ck_runs__owner_shape CHECK(
  (run_kind='chat' AND task_id IS NULL AND conversation_id IS NOT NULL AND
   session_id IS NOT NULL AND trigger_message_id IS NOT NULL AND context_message_seq IS NOT NULL)
  OR
  (run_kind='research' AND task_id IS NOT NULL AND session_id IS NULL AND (
    (conversation_id IS NULL AND trigger_message_id IS NULL AND context_message_seq IS NULL)
    OR
    (conversation_id IS NOT NULL AND trigger_message_id IS NOT NULL AND context_message_seq IS NOT NULL)
  ))
  OR
  (run_kind='file_job' AND task_id IS NOT NULL)
);

GRANT SELECT,INSERT,UPDATE ON tasks TO hpagent_api,hpagent_worker;
