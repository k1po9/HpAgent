SET search_path TO hpagent, public;

ALTER TABLE tasks ADD COLUMN output_entry_id uuid REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT;
ALTER TABLE tasks ADD CONSTRAINT ck_tasks__output_shape CHECK
  ((output_operation='create_child' AND output_entry_id IS NULL) OR
   (output_operation='update_content' AND output_entry_id IS NOT NULL));

ALTER TABLE research_run_save_intents ADD COLUMN target_entry_id uuid
  REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT;
ALTER TABLE research_run_save_intents ADD COLUMN expected_revision bigint;
ALTER TABLE research_run_save_intents ADD COLUMN expected_sha256 text;
ALTER TABLE research_run_save_intents ADD CONSTRAINT ck_research_run_save_intents__target CHECK
  ((operation='create_child' AND target_entry_id IS NULL) OR
   (operation='update_content' AND target_entry_id IS NOT NULL AND
    expected_revision IS NOT NULL AND expected_sha256 IS NOT NULL));
