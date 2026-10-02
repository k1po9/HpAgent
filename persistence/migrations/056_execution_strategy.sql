-- Phase 3: registered strategies, immutable execution input, PG due scheduling.
SET search_path TO hpagent, public;
ALTER TABLE work_requirements DROP CONSTRAINT work_requirements_capability_key_check;
ALTER TABLE work_requirements ADD CHECK(capability_key IN ('reminder','research_report','generic_work'));
-- Replace the anonymous versioned spec constraint using its column dependency.
DO $$ DECLARE c record; BEGIN
  FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='work_requirements'::regclass
    AND contype='c' AND pg_get_constraintdef(oid) LIKE '%work_json(spec,%' LOOP
    EXECUTE format('ALTER TABLE work_requirements DROP CONSTRAINT %I',c.conname);
  END LOOP;
END $$;
ALTER TABLE work_requirements ADD CHECK(work_json(spec,CASE capability_key
  WHEN 'reminder' THEN ARRAY['schema_version','content','target_ref']
  WHEN 'research_report' THEN ARRAY['schema_version','source_strategy','report_format']
  ELSE ARRAY['schema_version','reasoning_mode'] END,16384));
ALTER TABLE runs DROP CONSTRAINT ck_runs__strategy_owner;
ALTER TABLE runs ADD CONSTRAINT ck_runs__strategy_owner CHECK(
  source_kind='chat' AND strategy_kind='generic_agent' AND executor_key='chat_agent' OR
  source_kind='work' AND (strategy_kind='fixed_workflow' AND executor_key='research_report' OR
    strategy_kind='deterministic' AND executor_key='reminder' OR
    strategy_kind='generic_agent' AND executor_key='work_agent'));
ALTER TABLE runs DROP CONSTRAINT ck_runs__agent_strategy;
ALTER TABLE runs ADD CONSTRAINT ck_runs__agent_strategy CHECK(
  strategy_kind='generic_agent' AND agent_strategy IN ('react','plan_and_execute') AND agent_strategy IS NOT NULL OR
  strategy_kind<>'generic_agent' AND agent_strategy IS NULL);
ALTER TABLE runs ADD COLUMN input_snapshot jsonb NOT NULL DEFAULT '{"schema_version":1}';
ALTER TABLE runs ADD CHECK(jsonb_typeof(input_snapshot)='object' AND
  input_snapshot->>'schema_version'='1' AND octet_length(input_snapshot::text)<=65536);
CREATE FUNCTION immutable_strategy_input() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF NEW.input_snapshot IS DISTINCT FROM OLD.input_snapshot OR NEW.agent_strategy IS DISTINCT FROM OLD.agent_strategy THEN RAISE EXCEPTION 'immutable Run input'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_runs__strategy_input BEFORE UPDATE ON runs FOR EACH ROW EXECUTE FUNCTION immutable_strategy_input();
CREATE OR REPLACE FUNCTION create_root_execution() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=hpagent,pg_temp AS $$ BEGIN
  INSERT INTO run_executions(execution_id,account_id,run_id,status,context_manifest,resource_scope)
  VALUES(md5('hpagent:execution:root:'||NEW.run_id::text)::uuid,NEW.account_id,NEW.run_id,NEW.status,
    jsonb_strip_nulls(jsonb_build_object('schema_version',1,'source_kind',NEW.source_kind,
      'work_id',NEW.work_id,'requirement_revision',NEW.requirement_revision,
      'work_control_epoch',NEW.work_control_epoch,'conversation_id',NEW.conversation_id,
      'trigger_message_id',NEW.trigger_message_id,'context_message_seq',NEW.context_message_seq,
      'input_snapshot_ref','run:'||NEW.run_id::text,
      'checkpoint_version',NEW.input_snapshot->'checkpoint'->'checkpoint_version',
      'strategy',jsonb_build_object('strategy_kind',NEW.strategy_kind,'executor_key',NEW.executor_key,
        'executor_version',NEW.executor_version,'strategy_policy_version',NEW.strategy_policy_version))),
    jsonb_build_object('schema_version',1,'run_snapshot_ref',NEW.run_id));
  RETURN NEW;
END $$;
ALTER TABLE outbox_events DROP CONSTRAINT ck_outbox_events__event_type;
ALTER TABLE outbox_events ADD CONSTRAINT ck_outbox_events__event_type CHECK(event_type IN
  ('start_run','cancel_run','retain_memory','publish_terminal_event','publish_work_event','file_action_approval_decided'));

CREATE TABLE work_schedules (
  schedule_id uuid PRIMARY KEY, account_id uuid NOT NULL, work_id uuid NOT NULL,
  requirement_revision bigint NOT NULL, schedule_version bigint NOT NULL CHECK(schedule_version>=1),
  desired_enabled boolean NOT NULL, applied_version bigint NOT NULL DEFAULT 0 CHECK(applied_version>=0),
  timing jsonb NOT NULL, timezone text NOT NULL, next_due_at timestamptz,
  last_evaluated_at timestamptz, source_row_version bigint NOT NULL,
  UNIQUE(account_id,work_id), UNIQUE(account_id,work_id,schedule_id),
  FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_requirements(account_id,work_id,revision),
  CHECK(applied_version<=schedule_version),
  CHECK(work_json(timing,ARRAY['schema_version','kind','timezone','local_time','due_at','missed_fire_policy'],4096))
);
CREATE TABLE work_schedule_occurrences (
  account_id uuid NOT NULL, work_id uuid NOT NULL, schedule_id uuid NOT NULL,
  schedule_version bigint NOT NULL CHECK(schedule_version>=1), scheduled_for timestamptz NOT NULL,
  wakeup_id uuid, disposition text NOT NULL CHECK(disposition IN ('pending','skipped','superseded')),
  missed_from timestamptz, reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(schedule_id,schedule_version,scheduled_for),
  FOREIGN KEY(account_id,work_id,schedule_id) REFERENCES work_schedules(account_id,work_id,schedule_id),
  FOREIGN KEY(account_id,work_id,wakeup_id) REFERENCES work_wakeups(account_id,work_id,wakeup_id),
  CHECK((disposition='pending')=(wakeup_id IS NOT NULL))
);
CREATE TRIGGER tr_occurrence__immutable BEFORE UPDATE OR DELETE ON work_schedule_occurrences
  FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
CREATE TABLE reminder_intents (
  intent_id uuid PRIMARY KEY, account_id uuid NOT NULL, work_id uuid NOT NULL,
  run_id uuid NOT NULL, execution_id uuid NOT NULL, requirement_revision bigint NOT NULL,
  control_epoch bigint NOT NULL, operation_id text NOT NULL UNIQUE, content text NOT NULL,
  target_ref text NOT NULL CHECK(target_ref='account_inbox'),
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','cancelled')),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(account_id,run_id,execution_id,operation_id)
    REFERENCES execution_operations(account_id,run_id,execution_id,operation_id),
  FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_requirements(account_id,work_id,revision),
  CHECK(length(btrim(content)) BETWEEN 1 AND 12000)
);
CREATE FUNCTION validate_reminder_intent() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP='UPDATE' THEN
    IF NEW.state<>'cancelled' OR OLD.state<>'pending' OR
      (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN
      RAISE EXCEPTION 'immutable reminder intent'; END IF;
  ELSIF NOT EXISTS(SELECT 1 FROM runs r JOIN works w USING(account_id,work_id)
    WHERE r.account_id=NEW.account_id AND r.work_id=NEW.work_id AND r.run_id=NEW.run_id AND r.status='running'
      AND r.executor_key='reminder' AND r.requirement_revision=NEW.requirement_revision
      AND r.work_control_epoch=NEW.control_epoch AND w.status='active'
      AND w.active_coordinator_run_id=r.run_id AND w.current_requirement_revision=r.requirement_revision
      AND w.control_epoch=r.work_control_epoch) THEN RAISE EXCEPTION 'reminder intent fence mismatch';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_reminder_intent__fence BEFORE INSERT OR UPDATE ON reminder_intents
  FOR EACH ROW EXECUTE FUNCTION validate_reminder_intent();
GRANT SELECT,INSERT,UPDATE ON work_schedules TO hpagent_api,hpagent_worker;
GRANT SELECT,INSERT ON work_schedule_occurrences TO hpagent_worker;
GRANT SELECT,UPDATE ON reminder_intents TO hpagent_api;
GRANT SELECT,INSERT,UPDATE ON reminder_intents TO hpagent_worker;

CREATE INDEX ix_work_schedules__due ON work_schedules(next_due_at,schedule_id) WHERE desired_enabled;
CREATE FUNCTION validate_strategy_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE q work_requirements%ROWTYPE; w works%ROWTYPE; expected jsonb;
BEGIN
  IF NEW.source_kind='work' THEN
    SELECT * INTO q FROM work_requirements WHERE account_id=NEW.account_id
      AND work_id=NEW.work_id AND revision=NEW.requirement_revision;
    SELECT * INTO w FROM works WHERE account_id=NEW.account_id AND work_id=NEW.work_id;
    expected:=jsonb_build_object('objective',q.objective,'capability_key',q.capability_key,'spec',q.spec,
      'constraints',q.constraints,'acceptance_criteria',q.acceptance_criteria,'completion_mode',q.completion_mode,
      'timing',q.timing,'resource_requests',q.resource_requests,'deliverable_policy',q.deliverable_policy);
    IF NEW.input_snapshot->'requirement' IS DISTINCT FROM expected OR
      NEW.input_snapshot->'checkpoint' IS DISTINCT FROM w.checkpoint OR
      NEW.input_snapshot->>'requirement_revision' IS DISTINCT FROM NEW.requirement_revision::text OR
      NEW.input_snapshot->>'wakeup_id' IS DISTINCT FROM NEW.wakeup_id::text OR
      NEW.executor_key IS DISTINCT FROM (CASE q.capability_key WHEN 'reminder' THEN 'reminder'
        WHEN 'research_report' THEN 'research_report' ELSE 'work_agent' END) OR
      NEW.agent_strategy IS DISTINCT FROM (CASE WHEN q.capability_key='generic_work'
        THEN COALESCE(q.spec->>'reasoning_mode','react') ELSE NULL END) OR
      NEW.executor_version<>1 OR NEW.strategy_policy_version<>1 THEN
      RAISE EXCEPTION 'Run strategy/input must match fixed requirement and checkpoint'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_runs__strategy_snapshot BEFORE INSERT ON runs
  FOR EACH ROW EXECUTE FUNCTION validate_strategy_input();
CREATE FUNCTION validate_phase3_spec() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF NEW.capability_key='generic_work' AND COALESCE(NEW.spec->>'reasoning_mode','react') NOT IN ('react','plan_and_execute') OR
     NEW.capability_key='reminder' AND (COALESCE(NEW.spec->>'target_ref','account_inbox')<>'account_inbox'
       OR length(NEW.spec->>'content')>12000) OR
     NEW.timing ? 'missed_fire_policy' AND NEW.timing->>'missed_fire_policy'<>
       (CASE WHEN NEW.timing->>'kind'='daily' THEN 'latest' ELSE 'catch_up' END) THEN
    RAISE EXCEPTION 'unsupported capability spec or missed-fire policy'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_work_requirements__phase3 BEFORE INSERT ON work_requirements
  FOR EACH ROW EXECUTE FUNCTION validate_phase3_spec();
