SET search_path TO hpagent, public;

ALTER TABLE tasks ADD COLUMN output_directory_id uuid;
ALTER TABLE tasks ADD COLUMN output_operation text NOT NULL DEFAULT 'create_child'
  CHECK (output_operation IN ('create_child','update_content'));
ALTER TABLE tasks ADD COLUMN output_required boolean NOT NULL DEFAULT false;
ALTER TABLE tasks ADD COLUMN output_policy_version bigint NOT NULL DEFAULT 1;
ALTER TABLE tasks ADD CONSTRAINT fk_tasks__output_directory
  FOREIGN KEY(output_directory_id) REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT;
ALTER TABLE tasks ADD CONSTRAINT ck_tasks__required_directory
  CHECK (NOT output_required OR output_directory_id IS NOT NULL);

CREATE TABLE research_run_save_intents (
  run_id uuid PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
  account_id uuid NOT NULL,
  target_directory_id uuid NOT NULL REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT,
  operation text NOT NULL CHECK (operation IN ('create_child','update_content')),
  output_kind text NOT NULL CHECK (output_kind='research_markdown'),
  policy_version bigint NOT NULL,
  required boolean NOT NULL,
  operation_id text NOT NULL UNIQUE,
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','succeeded','failed')),
  entry_id uuid REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT,
  failure_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  saved_at timestamptz,
  FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT
);

CREATE TABLE research_history_baselines (
  run_id uuid PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
  account_id uuid NOT NULL,
  budget_bytes integer NOT NULL,
  selected jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(selected)='array'),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT
);

GRANT SELECT,INSERT,UPDATE ON research_run_save_intents,research_history_baselines
  TO hpagent_api,hpagent_worker;
