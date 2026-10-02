-- Target schema only: no historical Task migration or runtime compatibility.
SET search_path TO hpagent, public;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM tasks) OR EXISTS (SELECT 1 FROM runs) THEN
    RAISE EXCEPTION 'Durable Work foundation requires an empty development database; no historical backfill is supported';
  END IF;
END $$;

CREATE FUNCTION work_json(value jsonb, allowed text[], max_bytes integer) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT COALESCE(jsonb_typeof(value)='object' AND value->>'schema_version'='1'
    AND octet_length(value::text)<=max_bytes
    AND NOT EXISTS (SELECT 1 FROM jsonb_object_keys(value) k WHERE NOT k=ANY(allowed)),false)
$$;
CREATE TABLE works (
  work_id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES accounts(account_id),
  title varchar(200) NOT NULL CHECK(length(btrim(title))>0),
  status text NOT NULL DEFAULT 'active' CHECK(status IN
    ('active','pausing','paused','stopping','stopped','completed')),
  current_requirement_revision bigint NOT NULL DEFAULT 1 CHECK(current_requirement_revision>=1),
  row_version bigint NOT NULL DEFAULT 1 CHECK(row_version>=1),
  control_epoch bigint NOT NULL DEFAULT 0 CHECK(control_epoch>=0),
  active_coordinator_run_id uuid,
  continuation jsonb NOT NULL DEFAULT '{"schema_version":1,"kind":"ready","reason":"accepted"}',
  checkpoint jsonb NOT NULL DEFAULT '{"schema_version":1,"checkpoint_version":1,"requirement_revision":1,"facts":[],"decisions":[],"unresolved":[],"next_steps":[],"evidence_refs":[]}',
  completed_requirement_revision bigint, completion_receipt jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz, stopped_at timestamptz,
  UNIQUE(account_id,work_id),
  CHECK(work_json(continuation,ARRAY['schema_version','kind','reason','due_at','receipt_ref','operation_ref'],4096)
    AND continuation->>'kind' IN ('ready','at_time','awaiting_input','awaiting_delivery','retry_after','blocked','none')
    AND length(continuation->>'reason') BETWEEN 1 AND 500
    AND (continuation->>'kind' NOT IN ('at_time','retry_after') OR continuation->>'due_at' IS NOT NULL)),
  CHECK(work_json(checkpoint,ARRAY['schema_version','checkpoint_version','requirement_revision',
    'source_run_id','facts','decisions','unresolved','next_steps','evidence_refs','needs_review'],16384)
    AND (checkpoint->>'checkpoint_version')::bigint>=1
    AND (checkpoint->>'requirement_revision')::bigint>=1),
  CHECK((status='completed')=(completed_at IS NOT NULL)
    AND (status='completed')=(completed_requirement_revision IS NOT NULL)
    AND (status='completed')=(completion_receipt IS NOT NULL)
    AND (status='stopped')=(stopped_at IS NOT NULL)),
  CHECK(status NOT IN ('paused','stopped','completed') OR active_coordinator_run_id IS NULL),
  CHECK(updated_at>=created_at AND (completed_at IS NULL OR completed_at>=created_at)
    AND (stopped_at IS NULL OR stopped_at>=created_at))
);
CREATE TABLE work_requirements (
  account_id uuid NOT NULL, work_id uuid NOT NULL, revision bigint NOT NULL CHECK(revision>=1),
  objective text NOT NULL CHECK(length(btrim(objective)) BETWEEN 1 AND 20000),
  constraints jsonb NOT NULL CHECK(jsonb_typeof(constraints)='array'),
  acceptance_criteria jsonb NOT NULL CHECK(jsonb_typeof(acceptance_criteria)='array'),
  completion_mode text NOT NULL CHECK(completion_mode IN ('deliverable','ongoing')),
  capability_key text NOT NULL CHECK(capability_key IN ('reminder','research_report')),
  spec jsonb NOT NULL, timing jsonb NOT NULL, resource_requests jsonb NOT NULL,
  deliverable_policy jsonb NOT NULL,
  created_by text NOT NULL, source_message_id uuid, command_id uuid NOT NULL,
  change_reason varchar(500) NOT NULL, content_hash bytea NOT NULL CHECK(octet_length(content_hash)=32),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,work_id,revision),
  FOREIGN KEY(account_id,work_id) REFERENCES works(account_id,work_id),
  CHECK(work_json(spec,CASE capability_key WHEN 'reminder' THEN
    ARRAY['schema_version','content','target_ref'] ELSE
    ARRAY['schema_version','source_strategy','report_format'] END,16384)),
  CHECK(work_json(timing,ARRAY['schema_version','kind','timezone','local_time','due_at','missed_fire_policy'],4096)
    AND timing->>'kind' IN ('immediate','once','daily')),
  CHECK(work_json(deliverable_policy,ARRAY['schema_version','required','directory_id','entry_id','operation'],4096)),
  CHECK(jsonb_typeof(resource_requests)='array'
    AND octet_length((constraints || acceptance_criteria || resource_requests)::text)<=16384)
);
CREATE FUNCTION validate_work_requirement() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE criterion jsonb; requested jsonb; criterion_ids text[] := ARRAY[]::text[];
BEGIN
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.constraints) c
    WHERE jsonb_typeof(c)<>'string' OR length(c #>> '{}') NOT BETWEEN 1 AND 1000) THEN
    RAISE EXCEPTION 'invalid Work constraints'; END IF;
  FOR criterion IN SELECT value FROM jsonb_array_elements(NEW.acceptance_criteria) LOOP
    IF jsonb_typeof(criterion)<>'object' OR criterion->>'id' IS NULL
      OR length(criterion->>'id') NOT BETWEEN 1 AND 100
      OR criterion->>'id'=ANY(criterion_ids)
      OR jsonb_typeof(criterion->'required') IS DISTINCT FROM 'boolean'
      OR jsonb_typeof(criterion->'evidence_types') IS DISTINCT FROM 'array'
      OR jsonb_array_length(criterion->'evidence_types')=0
      OR EXISTS(SELECT 1 FROM jsonb_object_keys(criterion) k
        WHERE k<>ALL(ARRAY['id','required','evidence_types']))
      OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(criterion->'evidence_types') e
        WHERE e<>ALL(ARRAY['research_report','operation_receipt','user_acceptance'])) THEN
      RAISE EXCEPTION 'invalid Work acceptance criterion'; END IF;
    criterion_ids:=array_append(criterion_ids,criterion->>'id');
  END LOOP;
  FOR requested IN SELECT value FROM jsonb_array_elements(NEW.resource_requests) LOOP
    IF jsonb_typeof(requested) IS DISTINCT FROM 'object' OR requested->>'schema_version' IS DISTINCT FROM '1'
      OR requested->>'node_id' IS NULL OR jsonb_typeof(requested->'operations') IS DISTINCT FROM 'array'
      OR jsonb_array_length(requested->'operations')=0
      OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(requested->'operations') operation
        WHERE operation<>ALL(ARRAY['list_metadata','read_content','create_child',
                               'update_content','delete_entry'])) THEN
      RAISE EXCEPTION 'invalid Work resource request'; END IF;
  END LOOP;
  IF NEW.capability_key='reminder' AND COALESCE(length(btrim(NEW.spec->>'content')),0)<1 OR
     NEW.capability_key='research_report' AND jsonb_typeof(NEW.spec->'source_strategy') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid Work capability spec'; END IF;
  IF NEW.timing->>'kind'='once' AND (NEW.timing->>'due_at') IS NULL OR
     NEW.timing->>'kind'='daily' AND (NEW.completion_mode<>'ongoing' OR
       NEW.timing->>'local_time' !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$') OR
     NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=NEW.timing->>'timezone') THEN
    RAISE EXCEPTION 'invalid Work timing'; END IF;
  IF jsonb_typeof(NEW.deliverable_policy->'required') IS DISTINCT FROM 'boolean' OR
     NEW.deliverable_policy->>'required'='true' AND NEW.deliverable_policy->>'directory_id' IS NULL THEN
    RAISE EXCEPTION 'invalid Work deliverable policy'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_work_requirements__shape BEFORE INSERT ON work_requirements
  FOR EACH ROW EXECUTE FUNCTION validate_work_requirement();
ALTER TABLE works ADD FOREIGN KEY(account_id,work_id,current_requirement_revision)
  REFERENCES work_requirements(account_id,work_id,revision) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE works ADD FOREIGN KEY(account_id,work_id,completed_requirement_revision)
  REFERENCES work_requirements(account_id,work_id,revision);
ALTER TABLE messages ADD CONSTRAINT uq_messages__account_message UNIQUE(account_id,message_id);
ALTER TABLE idempotency_commands ADD UNIQUE(account_id,idempotency_command_id);
ALTER TABLE work_requirements ADD FOREIGN KEY(account_id,source_message_id)
  REFERENCES messages(account_id,message_id);
ALTER TABLE work_requirements ADD FOREIGN KEY(account_id,command_id)
  REFERENCES idempotency_commands(account_id,idempotency_command_id);

CREATE TABLE work_conversations (
  account_id uuid NOT NULL, work_id uuid NOT NULL, conversation_id uuid NOT NULL,
  linked_by_command_id uuid NOT NULL, source_message_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,work_id,conversation_id),
  FOREIGN KEY(account_id,work_id) REFERENCES works(account_id,work_id),
  FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id),
  FOREIGN KEY(account_id,linked_by_command_id) REFERENCES idempotency_commands(account_id,idempotency_command_id),
  FOREIGN KEY(account_id,conversation_id,source_message_id) REFERENCES messages(account_id,conversation_id,message_id)
);
CREATE TABLE work_events (
  event_id uuid PRIMARY KEY, account_id uuid NOT NULL, work_id uuid NOT NULL,
  event_seq bigint NOT NULL CHECK(event_seq>=1), event_type text NOT NULL CHECK(event_type IN
    ('accepted','revised','paused','pause_requested','resumed','stopped','stop_requested',
     'linked','advanced','run_succeeded','run_failed','run_cancelled','completed','result_accepted')),
  requirement_revision bigint NOT NULL, command_id uuid, run_id uuid,
  bounded_payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(account_id,work_id,event_seq),
  FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_requirements(account_id,work_id,revision),
  FOREIGN KEY(account_id,command_id) REFERENCES idempotency_commands(account_id,idempotency_command_id),
  CHECK(work_json(bounded_payload,ARRAY['schema_version','reason','status','conversation_id',
    'run_id','evidence','row_version','control_epoch'],16384))
);
CREATE TABLE work_wakeups (
  wakeup_id uuid PRIMARY KEY, account_id uuid NOT NULL, work_id uuid NOT NULL,
  trigger_key varchar(200) NOT NULL CHECK(length(trigger_key)>0),
  kind text NOT NULL CHECK(kind IN ('accepted','advance','revision','resume','retry','due')),
  expected_revision bigint NOT NULL, due_at timestamptz NOT NULL DEFAULT now(),
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','consumed','superseded','skipped')),
  run_id uuid, reason varchar(500) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(work_id,trigger_key), UNIQUE(account_id,work_id,wakeup_id),
  FOREIGN KEY(account_id,work_id,expected_revision) REFERENCES work_requirements(account_id,work_id,revision),
  CHECK((state='consumed')=(run_id IS NOT NULL))
);

ALTER TABLE runs DROP CONSTRAINT ck_runs__owner_shape;
ALTER TABLE runs DROP CONSTRAINT ck_runs__kind;
ALTER TABLE runs DROP CONSTRAINT fk_runs__tasks;
ALTER TABLE runs DROP COLUMN task_id;
ALTER TABLE runs RENAME COLUMN run_kind TO source_kind;
ALTER TABLE runs ADD COLUMN work_id uuid;
ALTER TABLE runs ADD COLUMN requirement_revision bigint;
ALTER TABLE runs ADD COLUMN work_control_epoch bigint;
ALTER TABLE runs ADD COLUMN wakeup_id uuid;
ALTER TABLE runs ADD COLUMN strategy_kind text NOT NULL DEFAULT 'generic_agent';
ALTER TABLE runs ADD COLUMN executor_key text NOT NULL DEFAULT 'chat_agent';
ALTER TABLE runs ADD COLUMN executor_version bigint NOT NULL DEFAULT 1 CHECK(executor_version>=1);
ALTER TABLE runs ADD COLUMN strategy_policy_version bigint NOT NULL DEFAULT 1 CHECK(strategy_policy_version>=1);
ALTER TABLE runs ADD COLUMN result_json jsonb;
ALTER TABLE runs ALTER COLUMN workflow_id DROP NOT NULL;
ALTER TABLE runs ADD CONSTRAINT ck_runs__source_kind CHECK(source_kind IN ('chat','work'));
ALTER TABLE runs DROP CONSTRAINT ck_runs__agent_strategy;
ALTER TABLE runs ADD CONSTRAINT ck_runs__agent_strategy CHECK(
  (source_kind='chat' AND agent_strategy IS NOT NULL AND agent_strategy IN ('react','plan_and_execute'))
  OR (source_kind='work' AND agent_strategy IS NULL));
ALTER TABLE runs ADD CONSTRAINT ck_runs__strategy CHECK(strategy_kind IN ('deterministic','fixed_workflow','generic_agent'));
ALTER TABLE runs ADD CONSTRAINT ck_runs__owner_shape CHECK(
  (source_kind='chat' AND conversation_id IS NOT NULL AND trigger_message_id IS NOT NULL
   AND context_message_seq IS NOT NULL AND work_id IS NULL AND requirement_revision IS NULL
   AND work_control_epoch IS NULL AND wakeup_id IS NULL)
  OR (source_kind='work' AND work_id IS NOT NULL AND requirement_revision IS NOT NULL AND requirement_revision>=1
   AND work_control_epoch IS NOT NULL AND work_control_epoch>=1 AND wakeup_id IS NOT NULL AND conversation_id IS NULL
   AND session_id IS NULL AND trigger_message_id IS NULL AND context_message_seq IS NULL));
ALTER TABLE runs ADD FOREIGN KEY(account_id,work_id,requirement_revision)
  REFERENCES work_requirements(account_id,work_id,revision);
ALTER TABLE runs ADD FOREIGN KEY(account_id,work_id,wakeup_id)
  REFERENCES work_wakeups(account_id,work_id,wakeup_id);
ALTER TABLE runs ADD UNIQUE(account_id,work_id,run_id);
ALTER TABLE runs ADD UNIQUE(account_id,work_id,run_id,requirement_revision);
ALTER TABLE runs ADD UNIQUE(wakeup_id);
ALTER TABLE runs ADD FOREIGN KEY(account_id,retry_of_run_id) REFERENCES runs(account_id,run_id);
ALTER TABLE works ADD FOREIGN KEY(account_id,work_id,active_coordinator_run_id)
  REFERENCES runs(account_id,work_id,run_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE work_events ADD FOREIGN KEY(account_id,work_id,run_id)
  REFERENCES runs(account_id,work_id,run_id);
ALTER TABLE work_wakeups ADD FOREIGN KEY(account_id,work_id,run_id)
  REFERENCES runs(account_id,work_id,run_id) DEFERRABLE INITIALLY DEFERRED;
DROP INDEX uq_runs__one_active_per_conversation;
CREATE UNIQUE INDEX uq_runs__one_active_per_conversation ON runs(conversation_id)
  WHERE source_kind='chat' AND status IN ('queued','running','cancelling');
CREATE UNIQUE INDEX uq_runs__one_active_per_work ON runs(work_id)
  WHERE source_kind='work' AND status IN ('queued','running','cancelling');
ALTER TABLE runs DROP CONSTRAINT ck_runs__status;
ALTER TABLE runs ADD CONSTRAINT ck_runs__status CHECK(status IN ('queued','running','cancelling','succeeded','failed','cancelled'));
ALTER TABLE runs DROP CONSTRAINT ck_runs__timestamps;
ALTER TABLE runs ADD CONSTRAINT ck_runs__timestamps CHECK(
  (started_at IS NULL OR started_at>=created_at) AND (finished_at IS NULL OR finished_at>=created_at)
  AND (status IN ('succeeded','failed','cancelled'))=(finished_at IS NOT NULL));
ALTER TABLE runs ADD CHECK(result_json IS NULL OR work_json(result_json,
  ARRAY['schema_version','kind','evidence','continuation','checkpoint'],16384));
ALTER TABLE runs ADD CONSTRAINT ck_runs__strategy_owner CHECK(
  (source_kind='chat' AND strategy_kind='generic_agent' AND executor_key='chat_agent') OR
  (source_kind='work' AND (
    strategy_kind='fixed_workflow' AND executor_key='research_report' OR
    strategy_kind='deterministic' AND executor_key='reminder')));
ALTER TABLE runs ADD CONSTRAINT ck_runs__work_result CHECK(source_kind<>'work' OR status<>'succeeded' OR
  (result_json IS NOT NULL AND result_json ? 'kind' AND jsonb_typeof(result_json->'evidence')='array'
   AND result_json->>'kind' IN ('deliverable_ready','progress_saved','waiting_input',
                                  'waiting_due','notification_enqueued')));

CREATE FUNCTION immutable_work_fact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable Work fact'; END $$;
CREATE TRIGGER tr_work_requirements__immutable BEFORE UPDATE OR DELETE ON work_requirements
  FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
CREATE TRIGGER tr_work_events__immutable BEFORE UPDATE OR DELETE ON work_events
  FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
CREATE FUNCTION enforce_work_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('stopped','completed') AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'terminal Work is immutable'; END IF;
  IF NEW.row_version<>OLD.row_version+1 OR NEW.control_epoch<OLD.control_epoch
    OR NEW.current_requirement_revision NOT IN (OLD.current_requirement_revision,OLD.current_requirement_revision+1)
    OR NEW.account_id<>OLD.account_id OR NEW.work_id<>OLD.work_id THEN
    RAISE EXCEPTION 'invalid Work version/identity'; END IF;
  IF (NEW.current_requirement_revision<>OLD.current_requirement_revision OR
      NEW.active_coordinator_run_id IS NOT NULL AND NEW.active_coordinator_run_id IS DISTINCT FROM OLD.active_coordinator_run_id OR
      NEW.status IS DISTINCT FROM OLD.status AND NEW.status IN ('pausing','paused','stopping','stopped'))
     AND NEW.control_epoch<=OLD.control_epoch THEN RAISE EXCEPTION 'control epoch must advance'; END IF;
  IF NEW.status<>OLD.status AND NOT (
    OLD.status='active' AND NEW.status IN ('pausing','paused','stopping','stopped','completed') OR
    OLD.status='pausing' AND NEW.status IN ('paused','stopping') OR
    OLD.status='paused' AND NEW.status IN ('active','stopping','stopped') OR
    OLD.status='stopping' AND NEW.status='stopped') THEN
    RAISE EXCEPTION 'invalid Work transition'; END IF;
  IF NEW.current_requirement_revision<>OLD.current_requirement_revision AND
    OLD.status NOT IN ('active','pausing','paused') THEN RAISE EXCEPTION 'Work cannot be revised'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_works__transition BEFORE UPDATE ON works
  FOR EACH ROW EXECUTE FUNCTION enforce_work_transition();
CREATE FUNCTION validate_work_coordinator() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; w works%ROWTYPE; r runs%ROWTYPE; n integer;
BEGIN
  target:=COALESCE(NEW.work_id,OLD.work_id);
  IF target IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO w FROM works WHERE work_id=target FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT count(*) INTO n FROM runs WHERE work_id=target AND status IN ('queued','running','cancelling');
  IF (w.active_coordinator_run_id IS NULL AND n<>0) OR
    (w.active_coordinator_run_id IS NOT NULL AND n<>1) THEN RAISE EXCEPTION 'Work coordinator mismatch'; END IF;
  IF w.active_coordinator_run_id IS NOT NULL THEN
    SELECT * INTO r FROM runs WHERE run_id=w.active_coordinator_run_id;
    IF r.status NOT IN ('queued','running','cancelling') OR r.work_id<>w.work_id OR r.account_id<>w.account_id
      OR (r.status<>'cancelling' AND (w.status<>'active' OR
       r.work_control_epoch<>w.control_epoch OR r.requirement_revision<>w.current_requirement_revision)) THEN
      RAISE EXCEPTION 'invalid active coordinator'; END IF;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ct_works__coordinator AFTER INSERT OR UPDATE ON works
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_work_coordinator();
CREATE CONSTRAINT TRIGGER ct_runs__coordinator AFTER INSERT OR UPDATE OR DELETE ON runs
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_work_coordinator();
CREATE FUNCTION immutable_run_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ROW(NEW.account_id,NEW.source_kind,NEW.conversation_id,NEW.session_id,NEW.trigger_message_id,
    NEW.context_message_seq,NEW.work_id,NEW.requirement_revision,NEW.work_control_epoch,NEW.wakeup_id,
    NEW.strategy_kind,NEW.executor_key,NEW.executor_version,NEW.strategy_policy_version,NEW.retry_of_run_id)
    IS DISTINCT FROM ROW(OLD.account_id,OLD.source_kind,OLD.conversation_id,OLD.session_id,OLD.trigger_message_id,
    OLD.context_message_seq,OLD.work_id,OLD.requirement_revision,OLD.work_control_epoch,OLD.wakeup_id,
    OLD.strategy_kind,OLD.executor_key,OLD.executor_version,OLD.strategy_policy_version,OLD.retry_of_run_id)
    OR (OLD.status IN ('succeeded','failed','cancelled') AND NEW IS DISTINCT FROM OLD) THEN
      RAISE EXCEPTION 'immutable Run input/terminal fact'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_runs__input BEFORE UPDATE ON runs FOR EACH ROW EXECUTE FUNCTION immutable_run_input();
CREATE OR REPLACE FUNCTION enforce_run_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status=OLD.status THEN RETURN NEW; END IF;
  IF OLD.status='queued' AND NEW.status IN ('running','cancelling','failed','cancelled') OR
     OLD.status='running' AND NEW.status IN ('cancelling','succeeded','failed') OR
     OLD.status='cancelling' AND NEW.status='cancelled' THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'invalid Run transition';
END $$;
-- Preserve the reverse dependency trigger from 008; update its validation helper.
CREATE OR REPLACE FUNCTION validate_run_message_invariant(target_run uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE; m messages%ROWTYPE; a messages%ROWTYPE;
BEGIN
  SELECT * INTO r FROM runs WHERE run_id=target_run FOR KEY SHARE;
  IF NOT FOUND THEN RETURN; END IF;
  IF r.source_kind='work' THEN
    IF EXISTS(SELECT 1 FROM messages WHERE produced_by_run_id=r.run_id) THEN
      RAISE EXCEPTION 'work Run cannot produce chat messages'; END IF;
    IF r.retry_of_run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM runs p
      WHERE p.run_id=r.retry_of_run_id AND p.account_id=r.account_id AND p.work_id=r.work_id
        AND p.status IN ('failed','cancelled')) THEN RAISE EXCEPTION 'invalid Work retry'; END IF;
    RETURN;
  END IF;
  SELECT * INTO m FROM messages WHERE message_id=r.trigger_message_id FOR KEY SHARE;
  IF NOT FOUND OR m.role<>'user' OR m.status<>'accepted' THEN RAISE EXCEPTION 'invalid chat trigger'; END IF;
  IF r.retry_of_run_id IS NULL AND r.context_message_seq<>m.sequence THEN RAISE EXCEPTION 'invalid chat watermark'; END IF;
  IF r.retry_of_run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM runs p WHERE p.run_id=r.retry_of_run_id
    AND p.source_kind='chat' AND p.status IN ('failed','cancelled')
    AND p.trigger_message_id=r.trigger_message_id AND p.context_message_seq=r.context_message_seq) THEN
    RAISE EXCEPTION 'invalid chat retry'; END IF;
  SELECT * INTO a FROM messages WHERE produced_by_run_id=r.run_id FOR KEY SHARE;
  IF NOT FOUND OR (r.status IN ('queued','running','cancelling') AND a.status<>'pending') OR
    (r.status='succeeded' AND a.status<>'completed') OR (r.status='failed' AND a.status<>'failed') OR
    (r.status='cancelled' AND a.status<>'aborted') THEN RAISE EXCEPTION 'Run/message state mismatch'; END IF;
END $$;

ALTER TABLE outbox_events ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE outbox_events ADD COLUMN work_id uuid;
ALTER TABLE outbox_events ADD COLUMN aggregate_type text NOT NULL DEFAULT 'run';
ALTER TABLE outbox_events ADD COLUMN aggregate_id uuid NOT NULL;
ALTER TABLE outbox_events ADD FOREIGN KEY(account_id,work_id) REFERENCES works(account_id,work_id);
ALTER TABLE outbox_events ADD FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id);
ALTER TABLE outbox_events ADD CHECK(
  (aggregate_type='run' AND run_id IS NOT NULL AND aggregate_id=run_id) OR
  (aggregate_type='work' AND run_id IS NULL AND conversation_id IS NULL AND work_id IS NOT NULL AND aggregate_id=work_id));
CREATE FUNCTION outbox_aggregate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.aggregate_type='run' THEN NEW.aggregate_id:=NEW.run_id; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_outbox__aggregate BEFORE INSERT ON outbox_events FOR EACH ROW EXECUTE FUNCTION outbox_aggregate();
ALTER TABLE outbox_events DROP CONSTRAINT ck_outbox_events__event_type;
ALTER TABLE outbox_events ADD CONSTRAINT ck_outbox_events__event_type CHECK(event_type IN
  ('start_run','start_research_run','cancel_run','retain_memory','publish_terminal_event','publish_work_event','file_action_approval_decided'));
ALTER TABLE outbox_events ADD CHECK((event_type='publish_work_event')=(aggregate_type='work'));
ALTER TABLE idempotency_commands DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands ADD CONSTRAINT ck_idempotency_commands__operation CHECK(operation IN
  ('create_conversation','send_message','cancel_run','retry_run','bind_identity','revoke_identity',
   'create_artifact','create_artifact_version','create_upload','create_workspace_upload','delete_file',
   'approve_file_action','reject_file_action','accept_work','revise_work','pause_work','resume_work',
   'stop_work','link_work','advance_work','accept_work_result'));

-- Redirect Research capability ownership; retained capability tables keep their contents/semantics.
ALTER TABLE research_plans DROP CONSTRAINT fk_research_plans__task;
ALTER TABLE source_records DROP CONSTRAINT fk_source_records__task;
ALTER TABLE research_plans RENAME COLUMN task_id TO work_id;
ALTER TABLE source_records RENAME COLUMN task_id TO work_id;
ALTER TABLE research_plans ADD COLUMN account_id uuid NOT NULL;
ALTER TABLE research_plans ADD COLUMN requirement_revision bigint NOT NULL;
ALTER TABLE source_records ADD COLUMN account_id uuid NOT NULL;
ALTER TABLE source_records ADD COLUMN requirement_revision bigint NOT NULL;
ALTER TABLE research_plans ADD FOREIGN KEY(account_id,work_id,run_id,requirement_revision)
  REFERENCES runs(account_id,work_id,run_id,requirement_revision);
ALTER TABLE source_records ADD FOREIGN KEY(account_id,work_id,run_id,requirement_revision)
  REFERENCES runs(account_id,work_id,run_id,requirement_revision);
ALTER TABLE workflow_executions ADD FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id);
ALTER TABLE run_budgets ADD FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id);
ALTER TABLE trace_runs ADD FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id);
ALTER TABLE artifacts ADD FOREIGN KEY(account_id,research_run_id) REFERENCES runs(account_id,run_id);
ALTER TABLE research_run_save_intents ADD COLUMN work_id uuid NOT NULL;
ALTER TABLE research_run_save_intents ADD COLUMN requirement_revision bigint NOT NULL;
ALTER TABLE research_run_save_intents ADD FOREIGN KEY(account_id,work_id,run_id,requirement_revision)
  REFERENCES runs(account_id,work_id,run_id,requirement_revision);
ALTER TABLE resource_policy_versions DROP CONSTRAINT resource_policy_versions_subject_kind_check;
ALTER TABLE resource_grants DROP CONSTRAINT resource_grants_subject_kind_check;
ALTER TABLE resource_policy_versions ADD CHECK(subject_kind IN ('conversation','work'));
ALTER TABLE resource_grants ADD CHECK(subject_kind IN ('conversation','work'));
CREATE OR REPLACE FUNCTION enforce_resource_subject_account() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE r runs%ROWTYPE; g resource_grants%ROWTYPE;
BEGIN
  IF TG_TABLE_NAME='resource_policy_versions' THEN
    IF NEW.subject_kind='conversation' THEN
      PERFORM 1 FROM conversations WHERE account_id=NEW.account_id AND conversation_id=NEW.subject_id;
    ELSE PERFORM 1 FROM works WHERE account_id=NEW.account_id AND work_id=NEW.subject_id; END IF;
    IF NOT FOUND THEN RAISE EXCEPTION 'resource subject belongs to another account'; END IF;
  ELSIF TG_TABLE_NAME='run_resource_snapshots' THEN
    SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
    IF NOT FOUND OR (NEW.subject_kind='conversation' AND r.conversation_id IS DISTINCT FROM NEW.subject_id)
      OR (NEW.subject_kind='work' AND r.work_id IS DISTINCT FROM NEW.subject_id) THEN
      RAISE EXCEPTION 'Run resource subject mismatch'; END IF;
  ELSE
    IF NEW.basis='workspace_grant' THEN
      SELECT * INTO g FROM resource_grants WHERE account_id=NEW.account_id AND grant_id=NEW.grant_id;
      IF NOT FOUND THEN RAISE EXCEPTION 'resource grant missing'; END IF;
      SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
      IF NOT FOUND OR (g.subject_kind='conversation' AND r.conversation_id IS DISTINCT FROM g.subject_id)
        OR (g.subject_kind='work' AND r.work_id IS DISTINCT FROM g.subject_id) THEN
        RAISE EXCEPTION 'resource grant subject mismatch'; END IF;
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TABLE tasks;
CREATE INDEX ix_works__account ON works(account_id,work_id DESC);
CREATE INDEX ix_work_wakeups__due ON work_wakeups(due_at,wakeup_id) WHERE state='pending';
GRANT SELECT,INSERT,UPDATE ON works,work_wakeups TO hpagent_api,hpagent_worker;
GRANT SELECT,INSERT ON work_requirements,work_events,work_conversations TO hpagent_api;
GRANT SELECT ON work_requirements,work_conversations TO hpagent_worker;
GRANT SELECT,INSERT ON work_events TO hpagent_worker;
REVOKE UPDATE,DELETE ON work_requirements,work_events,work_conversations FROM hpagent_api,hpagent_worker;

CREATE FUNCTION enforce_work_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE; q work_requirements%ROWTYPE; receipt jsonb;
BEGIN
  IF NEW.status<>'completed' OR OLD.status='completed' THEN RETURN NEW; END IF;
  receipt:=NEW.completion_receipt;
  IF NOT COALESCE(work_json(receipt,ARRAY['schema_version','run_id','requirement_revision','evidence','evaluator'],16384),false)
    OR NEW.completed_requirement_revision<>NEW.current_requirement_revision
    OR (receipt->>'requirement_revision')::bigint IS DISTINCT FROM NEW.current_requirement_revision THEN
    RAISE EXCEPTION 'invalid completion revision/receipt'; END IF;
  SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND work_id=NEW.work_id
    AND run_id=(receipt->>'run_id')::uuid;
  IF NOT FOUND OR r.status<>'succeeded' OR r.requirement_revision<>NEW.current_requirement_revision
    OR r.work_control_epoch<>NEW.control_epoch OR receipt->'evidence' IS DISTINCT FROM r.result_json->'evidence' THEN
    RAISE EXCEPTION 'completion evidence is not from current execution'; END IF;
  SELECT * INTO q FROM work_requirements WHERE account_id=NEW.account_id AND work_id=NEW.work_id
    AND revision=NEW.current_requirement_revision;
  IF q.completion_mode<>'deliverable' OR jsonb_array_length(q.acceptance_criteria)=0
    OR jsonb_typeof(receipt->'evidence') IS DISTINCT FROM 'array'
    OR jsonb_array_length(receipt->'evidence')=0 OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(q.acceptance_criteria) c
    WHERE (c->>'required')::boolean AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(receipt->'evidence') e
      WHERE e->>'criterion_id'=c->>'id' AND c->'evidence_types' ? (e->>'type')
        AND length(e->>'ref')>0)) THEN RAISE EXCEPTION 'required completion criteria missing'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(receipt->'evidence') e
    WHERE e->>'type'='research_report' AND NOT EXISTS(
      SELECT 1 FROM artifact_versions v JOIN artifacts a ON a.artifact_id=v.artifact_id
      WHERE v.artifact_version_id=(e->>'ref')::uuid AND v.account_id=r.account_id
        AND a.account_id=r.account_id AND a.research_run_id=r.run_id
        AND v.status='completed')) THEN
    RAISE EXCEPTION 'Research evidence does not resolve to this Run'; END IF;
  IF (q.deliverable_policy->>'required')::boolean AND NOT EXISTS (
    SELECT 1 FROM research_run_save_intents i WHERE i.account_id=NEW.account_id AND i.run_id=r.run_id
      AND i.work_id=NEW.work_id AND i.requirement_revision=q.revision AND i.required AND i.state='succeeded'
      AND CASE WHEN i.operation='create_child' THEN EXISTS(
        SELECT 1 FROM workspace_save_operations o WHERE o.account_id=i.account_id
        AND o.operation_id=i.operation_id AND o.node_id=i.entry_id AND o.source_run_id=r.run_id)
      ELSE EXISTS(SELECT 1 FROM workspace_version_operations o WHERE o.account_id=i.account_id
        AND o.operation_id=i.operation_id AND o.node_id=i.entry_id AND o.source_run_id=r.run_id) END
  ) THEN RAISE EXCEPTION 'required save receipt missing'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_works__completion BEFORE UPDATE ON works FOR EACH ROW EXECUTE FUNCTION enforce_work_completion();
CREATE FUNCTION enforce_wakeup_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ROW(NEW.account_id,NEW.work_id,NEW.trigger_key,NEW.kind,NEW.expected_revision,NEW.due_at)
    IS DISTINCT FROM ROW(OLD.account_id,OLD.work_id,OLD.trigger_key,OLD.kind,OLD.expected_revision,OLD.due_at)
    OR OLD.state<>'pending' AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'immutable wakeup identity/state'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_work_wakeups__transition BEFORE UPDATE ON work_wakeups
  FOR EACH ROW EXECUTE FUNCTION enforce_wakeup_transition();

ALTER TABLE workspace_save_operations DROP CONSTRAINT workspace_save_operations_source_kind_check;
ALTER TABLE workspace_version_operations DROP CONSTRAINT workspace_version_operations_source_kind_check;
ALTER TABLE workspace_save_operations DROP CONSTRAINT ck_workspace_save_operations__source;
ALTER TABLE workspace_version_operations DROP CONSTRAINT ck_workspace_version_operations__source;
ALTER TABLE workspace_save_operations ADD CHECK(source_kind IN ('user_manual','work_auto'));
ALTER TABLE workspace_version_operations ADD CHECK(source_kind IN ('user_manual','work_auto'));
ALTER TABLE workspace_save_operations ADD CHECK((source_kind='user_manual' AND source_run_id IS NULL)
  OR (source_kind='work_auto' AND source_run_id IS NOT NULL));
ALTER TABLE workspace_version_operations ADD CHECK((source_kind='user_manual' AND source_run_id IS NULL)
  OR (source_kind='work_auto' AND source_run_id IS NOT NULL));

CREATE OR REPLACE FUNCTION publish_research_artifact(target_run_id uuid,target_artifact_id uuid,
  target_version_id uuid,target_html text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
  SET search_path=hpagent,pg_temp AS $$
DECLARE r runs%ROWTYPE; w works%ROWTYPE; report_title text;
BEGIN
  SELECT works.* INTO w FROM works JOIN runs ON runs.account_id=works.account_id AND runs.work_id=works.work_id
    WHERE runs.run_id=target_run_id FOR UPDATE OF works;
  SELECT * INTO r FROM runs WHERE run_id=target_run_id FOR UPDATE;
  IF NOT FOUND OR r.executor_key<>'research_report' OR r.source_kind<>'work' OR r.status<>'running'
    OR w.status<>'active' OR w.active_coordinator_run_id<>r.run_id OR w.control_epoch<>r.work_control_epoch
    OR w.current_requirement_revision<>r.requirement_revision
    OR NOT EXISTS(SELECT 1 FROM accounts WHERE account_id=r.account_id AND status='active') THEN
    RAISE EXCEPTION 'Research publication authority invalid'; END IF;
  SELECT report_structured_json->>'title' INTO report_title FROM research_reports WHERE run_id=target_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Research report not found'; END IF;
  INSERT INTO artifacts(artifact_id,account_id,research_run_id,title)
    VALUES(target_artifact_id,r.account_id,target_run_id,left(COALESCE(report_title,'Research Report'),200))
    ON CONFLICT(artifact_id) DO NOTHING;
  INSERT INTO artifact_versions(artifact_version_id,artifact_id,account_id,version,status,html,started_at,completed_at)
    VALUES(target_version_id,target_artifact_id,r.account_id,1,'completed',target_html,now(),now())
    ON CONFLICT(artifact_version_id) DO NOTHING;
  IF NOT EXISTS(SELECT 1 FROM artifact_versions v JOIN artifacts a ON a.artifact_id=v.artifact_id
    WHERE v.account_id=r.account_id AND v.artifact_version_id=target_version_id
      AND a.research_run_id=r.run_id AND a.artifact_id=target_artifact_id) THEN
    RAISE EXCEPTION 'Research artifact provenance mismatch'; END IF;
  UPDATE research_reports SET artifact_id=target_artifact_id,artifact_version_id=target_version_id,updated_at=now()
    WHERE run_id=target_run_id;
  RETURN target_artifact_id;
END $$;
