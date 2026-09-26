SET search_path TO hpagent, public;

ALTER TABLE workspace_save_operations ADD COLUMN source_kind text NOT NULL DEFAULT 'user_manual'
  CHECK (source_kind IN ('user_manual','task_auto'));
ALTER TABLE workspace_save_operations ADD COLUMN source_run_id uuid
  REFERENCES runs(run_id) ON DELETE RESTRICT;
ALTER TABLE workspace_save_operations ADD CONSTRAINT ck_workspace_save_operations__source CHECK
  ((source_kind='user_manual' AND source_run_id IS NULL) OR
   (source_kind='task_auto' AND source_run_id IS NOT NULL));

ALTER TABLE workspace_version_operations ADD COLUMN source_kind text NOT NULL DEFAULT 'user_manual'
  CHECK (source_kind IN ('user_manual','task_auto'));
ALTER TABLE workspace_version_operations ADD COLUMN source_run_id uuid
  REFERENCES runs(run_id) ON DELETE RESTRICT;
ALTER TABLE workspace_version_operations ADD CONSTRAINT ck_workspace_version_operations__source CHECK
  ((source_kind='user_manual' AND source_run_id IS NULL) OR
   (source_kind='task_auto' AND source_run_id IS NOT NULL));
