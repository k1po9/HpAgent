-- Research R5/R6: structured daily diff and Temporal-backed Task schedules.
SET search_path TO hpagent, public;

ALTER TABLE tasks ADD COLUMN schedule_type text NOT NULL DEFAULT 'manual';
ALTER TABLE tasks ADD COLUMN schedule_timezone varchar(100) NOT NULL DEFAULT 'UTC';
ALTER TABLE tasks ADD COLUMN schedule_expression varchar(20);
ALTER TABLE tasks ADD COLUMN schedule_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE tasks ADD COLUMN schedule_version bigint NOT NULL DEFAULT 1;
ALTER TABLE tasks ADD COLUMN schedule_applied_version bigint NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD CONSTRAINT ck_tasks__schedule_type
  CHECK(schedule_type IN ('manual','daily'));
ALTER TABLE tasks ADD CONSTRAINT ck_tasks__schedule_shape CHECK(
  (schedule_type='manual' AND schedule_enabled=false)
  OR
  (schedule_type='daily' AND schedule_expression IS NOT NULL));
ALTER TABLE tasks ADD CONSTRAINT ck_tasks__schedule_versions CHECK(
  schedule_version >= 1 AND schedule_applied_version >= 0
  AND schedule_applied_version <= schedule_version);
CREATE INDEX ix_tasks__schedule_reconcile
  ON tasks(schedule_applied_version,schedule_version,task_id)
  WHERE schedule_applied_version < schedule_version;

ALTER TABLE research_claims ADD COLUMN evidence_status text NOT NULL DEFAULT 'pending';
ALTER TABLE research_claims ADD CONSTRAINT ck_research_claims__evidence_status
  CHECK(evidence_status IN ('pending','supported','weak','unsupported'));

ALTER TABLE research_reports ADD COLUMN previous_run_id uuid;
ALTER TABLE research_reports ADD COLUMN snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE research_reports ADD COLUMN daily_diff jsonb NOT NULL DEFAULT
  '{"new":[],"changed":[],"continuing":[],"invalidated":[]}'::jsonb;
ALTER TABLE research_reports ADD CONSTRAINT fk_research_reports__previous_run
  FOREIGN KEY(previous_run_id) REFERENCES runs(run_id) ON DELETE SET NULL;
ALTER TABLE research_reports ADD CONSTRAINT ck_research_reports__snapshot
  CHECK(jsonb_typeof(snapshot)='object');
ALTER TABLE research_reports ADD CONSTRAINT ck_research_reports__daily_diff
  CHECK(jsonb_typeof(daily_diff)='object');

ALTER TABLE idempotency_commands DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands ADD CONSTRAINT ck_idempotency_commands__operation
  CHECK(operation IN ('create_conversation','send_message','cancel_run','retry_run',
    'bind_identity','revoke_identity','create_artifact','create_artifact_version',
    'create_upload','delete_file','create_task','trigger_task','update_task_schedule'));

GRANT SELECT,UPDATE ON tasks TO hpagent_worker;
GRANT INSERT,UPDATE ON idempotency_commands TO hpagent_worker;
GRANT SELECT ON source_contents,research_claims,research_citations,research_reports TO hpagent_api;
