-- Research R0/R1/minimal-R2: Task-owned Runs, source records and evidence.
SET search_path TO hpagent, public;

CREATE TABLE tasks (
  task_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  task_type text NOT NULL DEFAULT 'research_report',
  title varchar(200) NOT NULL,
  objective text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  source_strategy jsonb NOT NULL DEFAULT '{}'::jsonb,
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_triggered_at timestamptz,
  CONSTRAINT fk_tasks__accounts FOREIGN KEY(account_id)
    REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT uq_tasks__account_task UNIQUE(account_id,task_id),
  CONSTRAINT ck_tasks__type CHECK(task_type IN ('research_report')),
  CONSTRAINT ck_tasks__status CHECK(status IN ('active','paused','archived')),
  CONSTRAINT ck_tasks__title CHECK(length(btrim(title)) BETWEEN 1 AND 200),
  CONSTRAINT ck_tasks__objective CHECK(length(btrim(objective)) BETWEEN 1 AND 20000),
  CONSTRAINT ck_tasks__strategy CHECK(jsonb_typeof(source_strategy)='object'),
  CONSTRAINT ck_tasks__timestamps CHECK(
    updated_at >= created_at AND
    (last_triggered_at IS NULL OR last_triggered_at >= created_at))
);
CREATE INDEX ix_tasks__account_list ON tasks(account_id,updated_at DESC,task_id DESC);

ALTER TABLE runs ADD COLUMN task_id uuid;
ALTER TABLE runs ADD COLUMN run_kind text NOT NULL DEFAULT 'chat';
ALTER TABLE runs ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE runs ALTER COLUMN session_id DROP NOT NULL;
ALTER TABLE runs ALTER COLUMN trigger_message_id DROP NOT NULL;
ALTER TABLE runs ALTER COLUMN context_message_seq DROP NOT NULL;
ALTER TABLE runs ADD CONSTRAINT fk_runs__tasks
  FOREIGN KEY(account_id,task_id) REFERENCES tasks(account_id,task_id) ON DELETE RESTRICT;
ALTER TABLE runs ADD CONSTRAINT ck_runs__kind CHECK(run_kind IN ('chat','research','file_job'));
ALTER TABLE runs ADD CONSTRAINT ck_runs__owner_shape CHECK(
  (run_kind='chat' AND task_id IS NULL AND conversation_id IS NOT NULL AND
   session_id IS NOT NULL AND trigger_message_id IS NOT NULL AND context_message_seq IS NOT NULL)
  OR
  (run_kind='research' AND task_id IS NOT NULL AND conversation_id IS NULL AND
   session_id IS NULL AND trigger_message_id IS NULL AND context_message_seq IS NULL)
  OR
  (run_kind='file_job' AND task_id IS NOT NULL)
);
CREATE INDEX ix_runs__task_history ON runs(account_id,task_id,created_at DESC,run_id DESC)
  WHERE task_id IS NOT NULL;
CREATE UNIQUE INDEX uq_runs__one_active_per_task ON runs(task_id)
  WHERE task_id IS NOT NULL AND status IN ('queued','running','cancelling');

-- Research Runs are intentionally not synthetic chat Runs. Existing composite
-- FKs remain authoritative for chat; these run_id FKs retain integrity for the
-- nullable conversation shape used by research.
ALTER TABLE workflow_executions ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE workflow_executions ADD CONSTRAINT fk_workflow_executions__run_id
  FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT;
ALTER TABLE outbox_events ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE outbox_events ADD CONSTRAINT fk_outbox_events__run_id
  FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT;
ALTER TABLE run_budgets ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE run_budgets ADD CONSTRAINT fk_run_budgets__run_id
  FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT;
ALTER TABLE trace_runs ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE trace_runs ADD CONSTRAINT fk_trace_runs__run_id
  FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE;

ALTER TABLE idempotency_commands DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands ADD CONSTRAINT ck_idempotency_commands__operation
  CHECK(operation IN ('create_conversation','send_message','cancel_run','retry_run',
    'bind_identity','revoke_identity','create_artifact','create_artifact_version',
    'create_upload','delete_file','create_task','trigger_task'));

ALTER TABLE outbox_events DROP CONSTRAINT ck_outbox_events__event_type;
ALTER TABLE outbox_events ADD CONSTRAINT ck_outbox_events__event_type CHECK(
  event_type IN ('start_run','start_research_run','cancel_run','retain_memory',
                 'publish_terminal_event'));

ALTER TABLE run_usage_ledger DROP CONSTRAINT ck_run_usage_ledger__dimension;
ALTER TABLE run_usage_ledger ADD CONSTRAINT ck_run_usage_ledger__dimension CHECK(
  dimension IN ('model_input_tokens','model_output_tokens','model_total_tokens','model_calls',
    'tool_calls','bytes_scanned','bytes_returned_to_model','bytes_written',
    'output_file_bytes','wall_time_ms','sources_discovered','source_fetches',
    'research_iterations'));

CREATE TABLE research_plans (
  run_id uuid PRIMARY KEY,
  task_id uuid NOT NULL,
  plan_version integer NOT NULL DEFAULT 1,
  plan jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_research_plans__run FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT fk_research_plans__task FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE RESTRICT,
  CONSTRAINT ck_research_plans__version CHECK(plan_version >= 1),
  CONSTRAINT ck_research_plans__json CHECK(jsonb_typeof(plan)='object')
);

CREATE TABLE source_records (
  source_id uuid PRIMARY KEY,
  run_id uuid NOT NULL,
  task_id uuid NOT NULL,
  provider varchar(100) NOT NULL,
  source_type varchar(50) NOT NULL,
  canonical_uri text NOT NULL,
  title text NOT NULL DEFAULT '',
  author text,
  publisher text,
  published_at timestamptz,
  fetched_at timestamptz,
  mime_type varchar(255),
  language varchar(64),
  content_ref text,
  content_hash char(64),
  source_tier smallint NOT NULL DEFAULT 3,
  fetch_status text NOT NULL DEFAULT 'discovered',
  duplicate_of_source_id uuid,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_source_records__run FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT fk_source_records__task FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE RESTRICT,
  CONSTRAINT fk_source_records__duplicate FOREIGN KEY(duplicate_of_source_id)
    REFERENCES source_records(source_id) ON DELETE RESTRICT,
  CONSTRAINT uq_source_records__run_uri UNIQUE(run_id,canonical_uri),
  CONSTRAINT ck_source_records__uri CHECK(length(btrim(canonical_uri)) > 0),
  CONSTRAINT ck_source_records__hash CHECK(content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_source_records__tier CHECK(source_tier BETWEEN 0 AND 3),
  CONSTRAINT ck_source_records__status CHECK(fetch_status IN ('discovered','fetched','duplicate','failed')),
  CONSTRAINT ck_source_records__metadata CHECK(jsonb_typeof(metadata)='object')
);
CREATE INDEX ix_source_records__run_status ON source_records(run_id,fetch_status,source_id);
CREATE INDEX ix_source_records__run_hash ON source_records(run_id,content_hash)
  WHERE content_hash IS NOT NULL;

CREATE TABLE source_contents (
  content_ref text PRIMARY KEY,
  source_id uuid NOT NULL UNIQUE,
  content_text text NOT NULL,
  byte_size bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_source_contents__source FOREIGN KEY(source_id)
    REFERENCES source_records(source_id) ON DELETE CASCADE,
  CONSTRAINT ck_source_contents__size CHECK(byte_size >= 0)
);

CREATE TABLE evidence_items (
  evidence_id uuid PRIMARY KEY,
  run_id uuid NOT NULL,
  source_id uuid NOT NULL,
  excerpt text NOT NULL,
  content_ref text NOT NULL,
  source_locator jsonb NOT NULL,
  source_quality smallint NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_evidence_items__run FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT fk_evidence_items__source FOREIGN KEY(source_id)
    REFERENCES source_records(source_id) ON DELETE RESTRICT,
  CONSTRAINT fk_evidence_items__content FOREIGN KEY(content_ref)
    REFERENCES source_contents(content_ref) ON DELETE RESTRICT,
  CONSTRAINT uq_evidence_items__source UNIQUE(run_id,source_id),
  CONSTRAINT ck_evidence_items__excerpt CHECK(length(btrim(excerpt)) > 0),
  CONSTRAINT ck_evidence_items__locator CHECK(jsonb_typeof(source_locator)='object'),
  CONSTRAINT ck_evidence_items__quality CHECK(source_quality BETWEEN 0 AND 3),
  CONSTRAINT ck_evidence_items__metadata CHECK(jsonb_typeof(metadata)='object')
);
CREATE INDEX ix_evidence_items__run ON evidence_items(run_id,created_at,evidence_id);

CREATE TABLE research_stage_results (
  operation_id varchar(200) PRIMARY KEY,
  run_id uuid NOT NULL,
  stage varchar(100) NOT NULL,
  result jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_research_stage_results__run FOREIGN KEY(run_id)
    REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT uq_research_stage_results__run_stage UNIQUE(run_id,stage),
  CONSTRAINT ck_research_stage_results__result CHECK(jsonb_typeof(result)='object')
);

-- Keep the evolved reverse-dependency trigger from migration 008 intact. Only
-- teach its validation helper that non-chat Runs intentionally have no message pair.
CREATE OR REPLACE FUNCTION validate_run_message_invariant(target_run uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE; trigger_msg messages%ROWTYPE; agent_msg messages%ROWTYPE;
BEGIN
  SELECT * INTO r FROM runs WHERE run_id = target_run FOR KEY SHARE;
  IF NOT FOUND OR r.run_kind <> 'chat' THEN RETURN; END IF;
  SELECT * INTO trigger_msg FROM messages WHERE message_id = r.trigger_message_id FOR KEY SHARE;
  IF NOT FOUND OR trigger_msg.role <> 'user' OR trigger_msg.status <> 'accepted' THEN
    RAISE EXCEPTION 'run trigger must be an accepted user message';
  END IF;
  IF r.retry_of_run_id IS NULL AND r.context_message_seq <> trigger_msg.sequence THEN
    RAISE EXCEPTION 'initial run context watermark must equal trigger sequence';
  END IF;
  IF r.retry_of_run_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM runs p WHERE p.run_id = r.retry_of_run_id
      AND p.status IN ('failed','cancelled')
      AND p.trigger_message_id = r.trigger_message_id
      AND p.context_message_seq = r.context_message_seq
  ) THEN RAISE EXCEPTION 'invalid retry source'; END IF;
  SELECT * INTO agent_msg FROM messages WHERE produced_by_run_id = r.run_id FOR KEY SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'each run must have exactly one assistant message'; END IF;
  IF (r.status IN ('queued','running','cancelling') AND agent_msg.status <> 'pending')
    OR (r.status = 'completed' AND agent_msg.status <> 'completed')
    OR (r.status = 'failed' AND agent_msg.status <> 'failed')
    OR (r.status = 'cancelled' AND agent_msg.status <> 'aborted') THEN
    RAISE EXCEPTION 'run and assistant states disagree';
  END IF;
END $$;

GRANT SELECT,INSERT,UPDATE ON tasks TO hpagent_api,hpagent_worker;
GRANT SELECT,INSERT,UPDATE ON research_plans,source_records,source_contents,
  evidence_items,research_stage_results TO hpagent_worker;
GRANT SELECT ON research_plans,source_records,evidence_items TO hpagent_api;
