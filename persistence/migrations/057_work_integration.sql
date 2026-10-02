-- Durable Work V1 Phase 4: resource references, governance and independent delivery.
SET search_path TO hpagent, public;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM works) OR EXISTS(SELECT 1 FROM artifact_versions) THEN
  RAISE EXCEPTION 'Phase 4 requires an empty development Work/Artifact store; no historical backfill';
 END IF;
END $$;
CREATE TABLE work_budgets (
 account_id uuid NOT NULL, work_id uuid NOT NULL, limits jsonb NOT NULL, used jsonb NOT NULL DEFAULT '{}',
 reserved jsonb NOT NULL DEFAULT '{}', version bigint NOT NULL DEFAULT 1 CHECK(version>=1),
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(account_id,work_id),
 FOREIGN KEY(account_id,work_id) REFERENCES works(account_id,work_id),
 CHECK(jsonb_typeof(limits)='object' AND jsonb_typeof(used)='object' AND jsonb_typeof(reserved)='object')
);
CREATE TABLE work_usage_ledger (
 account_id uuid NOT NULL, work_id uuid NOT NULL, run_id uuid NOT NULL, operation_id varchar(200) NOT NULL,
 dimension text NOT NULL, state text NOT NULL CHECK(state IN ('reserved','settled','released')),
 reserved_amount bigint NOT NULL CHECK(reserved_amount>=0), actual_amount bigint CHECK(actual_amount>=0),
 usage_source text CHECK(usage_source IN ('provider','measured','estimated')), created_at timestamptz NOT NULL DEFAULT now(),
 settled_at timestamptz, PRIMARY KEY(account_id,work_id,run_id,operation_id,dimension),
 FOREIGN KEY(account_id,work_id) REFERENCES work_budgets(account_id,work_id),
 FOREIGN KEY(account_id,work_id,run_id) REFERENCES runs(account_id,work_id,run_id)
);
CREATE TABLE capacity_queue (
 ticket_id uuid PRIMARY KEY, account_id uuid NOT NULL, run_id uuid NOT NULL, resource text NOT NULL,
 lane text NOT NULL CHECK(lane IN ('interactive','background')), state text NOT NULL DEFAULT 'waiting'
 CHECK(state IN ('waiting','held','released')), lease_until timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id)
);
CREATE TABLE capacity_turns (
 resource text NOT NULL, lane text NOT NULL, account_id uuid NOT NULL REFERENCES accounts(account_id),
 last_admitted_at timestamptz NOT NULL DEFAULT '-infinity', PRIMARY KEY(resource,lane,account_id)
);
CREATE INDEX ix_capacity_queue__active ON capacity_queue(resource,lane,account_id,state,created_at) WHERE state<>'released';
CREATE TABLE work_input_refs (
 ref_id uuid PRIMARY KEY, account_id uuid NOT NULL, work_id uuid NOT NULL, file_id uuid NOT NULL,
 source_message_id uuid, purpose varchar(200) NOT NULL, available boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
 UNIQUE(account_id,work_id,file_id), FOREIGN KEY(account_id,work_id) REFERENCES works(account_id,work_id),
 FOREIGN KEY(account_id,file_id) REFERENCES stored_files(account_id,file_id),
 FOREIGN KEY(account_id,source_message_id) REFERENCES messages(account_id,message_id),
 CHECK(available=(revoked_at IS NULL))
);
ALTER TABLE artifacts DROP CONSTRAINT ck_artifacts__owner_shape;
ALTER TABLE artifacts DROP COLUMN research_run_id;
ALTER TABLE artifacts ADD CONSTRAINT ck_artifacts__optional_source CHECK((conversation_id IS NULL)=(source_message_id IS NULL));
ALTER TABLE artifact_versions ADD COLUMN producing_run_id uuid;
ALTER TABLE artifact_versions ADD COLUMN producing_execution_id uuid;
ALTER TABLE artifact_versions ADD COLUMN producing_operation_id text;
ALTER TABLE artifact_versions ADD COLUMN source_markdown text;
ALTER TABLE artifact_versions ADD COLUMN file_id uuid;
ALTER TABLE artifact_versions ADD UNIQUE(account_id,artifact_version_id);
ALTER TABLE artifact_versions ADD FOREIGN KEY(account_id,producing_run_id,producing_execution_id)
 REFERENCES run_executions(account_id,run_id,execution_id);
ALTER TABLE artifact_versions ADD FOREIGN KEY(account_id,producing_run_id,producing_execution_id,producing_operation_id)
 REFERENCES execution_operations(account_id,run_id,execution_id,operation_id);
ALTER TABLE artifact_versions ADD FOREIGN KEY(account_id,file_id) REFERENCES stored_files(account_id,file_id);
ALTER TABLE artifact_versions ADD CHECK((producing_run_id IS NULL)=(producing_execution_id IS NULL));
ALTER TABLE work_events ADD UNIQUE(account_id,work_id,event_id);
CREATE TABLE work_artifacts (
 reference_id uuid PRIMARY KEY, account_id uuid NOT NULL, work_id uuid NOT NULL, artifact_version_id uuid NOT NULL,
 source_requirement_revision bigint NOT NULL, role text NOT NULL CHECK(role IN ('input','evidence','deliverable')),
 accepted_for_revision bigint, acceptance_event_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(account_id,work_id,source_requirement_revision) REFERENCES work_requirements(account_id,work_id,revision),
 FOREIGN KEY(account_id,work_id,accepted_for_revision) REFERENCES work_requirements(account_id,work_id,revision),
 FOREIGN KEY(account_id,artifact_version_id) REFERENCES artifact_versions(account_id,artifact_version_id),
 FOREIGN KEY(account_id,work_id,acceptance_event_id) REFERENCES work_events(account_id,work_id,event_id),
 CHECK((accepted_for_revision IS NULL)=(acceptance_event_id IS NULL)),
 CONSTRAINT uq_work_artifacts__reference UNIQUE NULLS NOT DISTINCT(account_id,work_id,artifact_version_id,source_requirement_revision,role,accepted_for_revision)
);
CREATE TRIGGER tr_work_artifacts__immutable BEFORE UPDATE OR DELETE ON work_artifacts FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
CREATE TABLE delivery_targets (
 target_id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES accounts(account_id), work_id uuid,
 channel text NOT NULL CHECK(channel IN ('web','napcat','official_qq')), identity_provider text,
 identity_subject text, route jsonb NOT NULL DEFAULT '{}', audience text NOT NULL CHECK(audience IN ('private','group')),
 content_scope text NOT NULL CHECK(content_scope IN ('summary','content')), target_version bigint NOT NULL CHECK(target_version>=1),
 enabled boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
 selected_by_command_id uuid, FOREIGN KEY(account_id,selected_by_command_id) REFERENCES idempotency_commands(account_id,idempotency_command_id),
 UNIQUE(account_id,target_id), FOREIGN KEY(account_id,work_id) REFERENCES works(account_id,work_id),
 CHECK(channel='web' AND audience='private' AND identity_provider IS NULL AND identity_subject IS NULL OR
       channel<>'web' AND identity_provider IS NOT NULL AND identity_subject IS NOT NULL),
 CHECK(audience<>'group' OR content_scope='summary')
);
ALTER TABLE messages ADD UNIQUE(account_id,produced_by_run_id,message_id);
CREATE TABLE notifications (
 notification_id uuid PRIMARY KEY, account_id uuid NOT NULL, work_id uuid, requirement_revision bigint,
 control_epoch bigint, run_id uuid, execution_id uuid, operation_id text, source_event_id uuid,
 source_message_id uuid, purpose text NOT NULL CHECK(purpose IN ('fulfillment','fact')), business_key varchar(300) NOT NULL UNIQUE,
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(account_id,notification_id),
 FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id),
 FOREIGN KEY(account_id,run_id,source_message_id) REFERENCES messages(account_id,produced_by_run_id,message_id),
 FOREIGN KEY(account_id,work_id,run_id,requirement_revision) REFERENCES runs(account_id,work_id,run_id,requirement_revision),
 FOREIGN KEY(account_id,run_id,execution_id,operation_id) REFERENCES execution_operations(account_id,run_id,execution_id,operation_id),
 FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_requirements(account_id,work_id,revision),
 FOREIGN KEY(account_id,work_id,source_event_id) REFERENCES work_events(account_id,work_id,event_id),
 CHECK(jsonb_typeof(payload)='object' AND payload->>'schema_version'='1' AND octet_length(payload::text)<=32768),
 CHECK(purpose<>'fulfillment' OR work_id IS NOT NULL AND control_epoch IS NOT NULL AND requirement_revision IS NOT NULL
  AND run_id IS NOT NULL AND execution_id IS NOT NULL AND operation_id IS NOT NULL)
);
CREATE TRIGGER tr_notifications__immutable BEFORE UPDATE OR DELETE ON notifications FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
CREATE TABLE deliveries (
 delivery_id uuid PRIMARY KEY, account_id uuid NOT NULL, notification_id uuid NOT NULL, target_id uuid NOT NULL,
 target_version bigint NOT NULL, state text NOT NULL DEFAULT 'pending'
 CHECK(state IN ('pending','sending','accepted','failed','uncertain','cancelled')),
 next_part integer NOT NULL DEFAULT 0 CHECK(next_part>=0), attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 provider_receipt jsonb, lease_token uuid, lease_until timestamptz, last_error text,
 available_at timestamptz NOT NULL DEFAULT now(), accepted_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(notification_id,target_id), FOREIGN KEY(account_id,notification_id) REFERENCES notifications(account_id,notification_id),
 FOREIGN KEY(account_id,target_id) REFERENCES delivery_targets(account_id,target_id),
 CHECK((state='accepted')=(accepted_at IS NOT NULL)),
 CHECK(state<>'accepted' OR provider_receipt IS NOT NULL)
);
CREATE INDEX ix_deliveries__claim ON deliveries(available_at,delivery_id) WHERE state='pending';
CREATE FUNCTION immutable_delivery_target() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (to_jsonb(NEW)-'enabled') IS DISTINCT FROM (to_jsonb(OLD)-'enabled') THEN RAISE EXCEPTION 'immutable target version'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_delivery_targets__immutable BEFORE UPDATE ON delivery_targets FOR EACH ROW EXECUTE FUNCTION immutable_delivery_target();
CREATE FUNCTION validate_delivery_transition() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF ROW(NEW.account_id,NEW.notification_id,NEW.target_id,NEW.target_version) IS DISTINCT FROM
    ROW(OLD.account_id,OLD.notification_id,OLD.target_id,OLD.target_version) OR
    OLD.state IN ('accepted','cancelled') AND NEW IS DISTINCT FROM OLD OR
    NEW.next_part<OLD.next_part OR NEW.next_part>OLD.next_part+1 THEN RAISE EXCEPTION 'invalid delivery transition'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_deliveries__transition BEFORE UPDATE ON deliveries FOR EACH ROW EXECUTE FUNCTION validate_delivery_transition();
ALTER TABLE deliveries ADD UNIQUE(account_id,delivery_id);
CREATE TABLE delivery_decisions (
 decision_id uuid PRIMARY KEY, account_id uuid NOT NULL, delivery_id uuid NOT NULL, command_id uuid,
 outcome text NOT NULL CHECK(outcome IN ('accepted','not_sent','retry_accepting_duplicate_risk')),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(account_id,delivery_id) REFERENCES deliveries(account_id,delivery_id),
 FOREIGN KEY(account_id,command_id) REFERENCES idempotency_commands(account_id,idempotency_command_id)
);
CREATE TRIGGER tr_delivery_decisions__immutable BEFORE UPDATE OR DELETE ON delivery_decisions FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
GRANT SELECT,INSERT ON delivery_decisions TO hpagent_api,hpagent_worker;
DROP TABLE reminder_intents;
DROP TABLE qq_deliveries;
DROP TABLE artifact_outbox_events;
GRANT SELECT,INSERT,UPDATE ON work_budgets,work_input_refs,delivery_targets,deliveries TO hpagent_api,hpagent_worker;
GRANT SELECT,INSERT ON work_artifacts,notifications TO hpagent_api,hpagent_worker;
GRANT SELECT ON work_usage_ledger TO hpagent_api;
GRANT SELECT,INSERT,UPDATE ON work_usage_ledger,capacity_queue,capacity_turns TO hpagent_worker;
GRANT SELECT,INSERT,UPDATE ON capacity_turns TO hpagent_api;
GRANT INSERT ON artifacts,artifact_versions TO hpagent_worker;
GRANT UPDATE ON artifact_versions TO hpagent_api;
CREATE OR REPLACE FUNCTION enforce_work_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE; q work_requirements%ROWTYPE; receipt jsonb;
BEGIN
  IF NEW.status<>'completed' OR OLD.status='completed' THEN RETURN NEW; END IF;
  receipt:=NEW.completion_receipt;
  IF receipt->>'evaluator' NOT IN ('work_completion_policy_v1','authorized_user_acceptance_v1','delivery_receipt_policy_v1') THEN
    RAISE EXCEPTION 'unsupported completion evaluator'; END IF;
  IF NOT COALESCE(work_json(receipt,ARRAY['schema_version','run_id','requirement_revision','evidence','evaluator'],16384),false)
    OR NEW.completed_requirement_revision<>NEW.current_requirement_revision
    OR (receipt->>'requirement_revision')::bigint IS DISTINCT FROM NEW.current_requirement_revision THEN
    RAISE EXCEPTION 'invalid completion revision/receipt'; END IF;
  SELECT * INTO q FROM work_requirements WHERE account_id=NEW.account_id AND work_id=NEW.work_id AND revision=NEW.current_requirement_revision;
  SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND work_id=NEW.work_id
    AND run_id=(receipt->>'run_id')::uuid;
  IF NOT FOUND OR r.status<>'succeeded' OR r.requirement_revision<>NEW.current_requirement_revision
    OR r.work_control_epoch<>NEW.control_epoch OR (q.capability_key IS DISTINCT FROM 'reminder' AND receipt->>'evaluator' NOT IN ('authorized_user_acceptance_v1','delivery_receipt_policy_v1') AND receipt->'evidence' IS DISTINCT FROM r.result_json->'evidence') THEN
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
        AND a.account_id=r.account_id AND v.producing_run_id=r.run_id
        AND v.status='completed')) THEN
    RAISE EXCEPTION 'Research evidence does not resolve to this Run'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(receipt->'evidence') e
    WHERE e->>'type' IN ('delivery_receipt','artifact_version','operation_receipt') AND NOT CASE e->>'type'
      WHEN 'artifact_version' THEN EXISTS(SELECT 1 FROM artifact_versions v WHERE v.account_id=NEW.account_id
        AND v.artifact_version_id=(e->>'ref')::uuid AND v.status='completed' AND (v.producing_run_id=r.run_id OR
        EXISTS(SELECT 1 FROM work_artifacts a WHERE a.account_id=NEW.account_id AND a.work_id=NEW.work_id
          AND a.artifact_version_id=v.artifact_version_id AND a.source_requirement_revision=q.revision
          AND a.role IN ('input','evidence') AND r.input_snapshot->'artifact_refs' @>
            jsonb_build_array(jsonb_build_object('artifact_version_id',v.artifact_version_id::text,'role',a.role)))))
      WHEN 'operation_receipt' THEN EXISTS(SELECT 1 FROM execution_operations o JOIN execution_result_receipts er USING(operation_id)
        WHERE o.account_id=NEW.account_id AND o.run_id=r.run_id AND (o.operation_id=e->>'ref' OR o.result_ref=e->>'ref')
        AND o.status='completed' AND er.disposition='current')
      ELSE EXISTS(SELECT 1 FROM deliveries d JOIN notifications n USING(account_id,notification_id)
        JOIN delivery_targets t USING(account_id,target_id) WHERE d.account_id=NEW.account_id
        AND d.delivery_id=(e->>'ref')::uuid AND d.state='accepted' AND n.work_id=NEW.work_id
        AND n.requirement_revision=NEW.current_requirement_revision AND n.control_epoch=NEW.control_epoch
        AND n.run_id=r.run_id AND OLD.continuation->>'receipt_ref'=n.notification_id::text
        AND t.enabled AND t.target_version=d.target_version) END) THEN
    RAISE EXCEPTION 'acceptance receipt provenance mismatch'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(receipt->'evidence') e WHERE e->>'type'='user_acceptance' AND NOT EXISTS(
    SELECT 1 FROM work_result_acceptances a WHERE a.account_id=NEW.account_id AND a.work_id=NEW.work_id
    AND a.run_id=r.run_id AND a.requirement_revision=q.revision AND e->>'ref'=a.acceptance_id::text)) THEN
    RAISE EXCEPTION 'user acceptance provenance mismatch'; END IF;
  IF receipt->>'evaluator'='authorized_user_acceptance_v1' AND NOT EXISTS(
    SELECT 1 FROM work_result_acceptances a WHERE a.account_id=NEW.account_id AND a.work_id=NEW.work_id
    AND a.run_id=r.run_id AND a.requirement_revision=q.revision AND EXISTS(
      SELECT 1 FROM jsonb_array_elements(receipt->'evidence') e WHERE e->>'type'='user_acceptance' AND e->>'ref'=a.acceptance_id::text)) THEN
    RAISE EXCEPTION 'authorized user acceptance missing'; END IF;
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

ALTER TABLE work_requirements DROP CONSTRAINT work_requirements_capability_key_check;
ALTER TABLE work_requirements ADD CHECK(capability_key IN ('reminder','research_report','generic_work','artifact_build'));
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='work_requirements'::regclass AND contype='c'
 AND pg_get_constraintdef(oid) LIKE '%work_json(spec,%' LOOP
 EXECUTE format('ALTER TABLE work_requirements DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE work_requirements ADD CHECK(work_json(spec,CASE capability_key
 WHEN 'reminder' THEN ARRAY['schema_version','content','target_ref']
 WHEN 'research_report' THEN ARRAY['schema_version','source_strategy','report_format']
 WHEN 'artifact_build' THEN ARRAY['schema_version','artifact_version_id']
 ELSE ARRAY['schema_version','reasoning_mode'] END,16384));
ALTER TABLE runs DROP CONSTRAINT ck_runs__strategy_owner;
ALTER TABLE runs ADD CONSTRAINT ck_runs__strategy_owner CHECK(
 source_kind='chat' AND strategy_kind='generic_agent' AND executor_key='chat_agent' OR
 source_kind='work' AND (strategy_kind='fixed_workflow' AND executor_key IN ('research_report','artifact_html') OR
 strategy_kind='deterministic' AND executor_key='reminder' OR strategy_kind='generic_agent' AND executor_key='work_agent'));
CREATE OR REPLACE FUNCTION validate_strategy_input() RETURNS trigger LANGUAGE plpgsql AS $$
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
        WHEN 'research_report' THEN 'research_report' WHEN 'artifact_build' THEN 'artifact_html' ELSE 'work_agent' END) OR
      NEW.agent_strategy IS DISTINCT FROM (CASE WHEN q.capability_key='generic_work'
        THEN COALESCE(q.spec->>'reasoning_mode','react') ELSE NULL END) OR
      NEW.executor_version<>1 OR NEW.strategy_policy_version<>1 THEN
      RAISE EXCEPTION 'Run strategy/input must match fixed requirement and checkpoint'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION validate_phase3_spec() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF NEW.capability_key='generic_work' AND COALESCE(NEW.spec->>'reasoning_mode','react') NOT IN ('react','plan_and_execute') OR
     NEW.capability_key='reminder' AND ((COALESCE(NEW.spec->>'target_ref','account_inbox')<>'account_inbox' AND NOT EXISTS(SELECT 1 FROM delivery_targets t WHERE t.account_id=NEW.account_id AND t.work_id=NEW.work_id AND t.target_id::text=NEW.spec->>'target_ref' AND t.enabled))
       OR length(NEW.spec->>'content')>12000) OR
     NEW.timing ? 'missed_fire_policy' AND NEW.timing->>'missed_fire_policy'<>
       (CASE WHEN NEW.timing->>'kind'='daily' THEN 'latest' ELSE 'catch_up' END) THEN
    RAISE EXCEPTION 'unsupported capability spec or missed-fire policy'; END IF;
  RETURN NEW;
END $$;
CREATE TABLE work_result_acceptances (
 acceptance_id uuid PRIMARY KEY, account_id uuid NOT NULL, work_id uuid NOT NULL, requirement_revision bigint NOT NULL,
 run_id uuid NOT NULL, artifact_version_id uuid NOT NULL, command_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(account_id,work_id,run_id,requirement_revision) REFERENCES runs(account_id,work_id,run_id,requirement_revision),
 FOREIGN KEY(account_id,artifact_version_id) REFERENCES artifact_versions(account_id,artifact_version_id),
 FOREIGN KEY(account_id,command_id) REFERENCES idempotency_commands(account_id,idempotency_command_id)
);
CREATE TRIGGER tr_work_acceptances__immutable BEFORE UPDATE OR DELETE ON work_result_acceptances FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
GRANT SELECT,INSERT ON work_result_acceptances TO hpagent_api,hpagent_worker;
DROP FUNCTION publish_research_artifact(uuid,uuid,uuid,text);
ALTER TABLE trace_runs ADD COLUMN work_id uuid;
ALTER TABLE trace_runs ADD COLUMN requirement_revision bigint;
ALTER TABLE trace_runs ADD COLUMN execution_id uuid;
ALTER TABLE model_input_snapshots ADD COLUMN work_id uuid;
ALTER TABLE model_input_snapshots ADD COLUMN requirement_revision bigint;
ALTER TABLE model_input_snapshots ADD COLUMN execution_id uuid;
ALTER TABLE trace_runs ADD FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_requirements(account_id,work_id,revision);
ALTER TABLE model_input_snapshots ADD FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_requirements(account_id,work_id,revision);
ALTER TABLE trace_runs ADD FOREIGN KEY(account_id,run_id,execution_id) REFERENCES run_executions(account_id,run_id,execution_id);
ALTER TABLE model_input_snapshots ADD FOREIGN KEY(account_id,run_id,execution_id) REFERENCES run_executions(account_id,run_id,execution_id);
CREATE FUNCTION bind_execution_observation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 SELECT r.work_id,r.requirement_revision,e.execution_id INTO NEW.work_id,NEW.requirement_revision,NEW.execution_id
 FROM runs r JOIN run_executions e USING(account_id,run_id) WHERE r.account_id=NEW.account_id AND r.run_id=NEW.run_id AND e.role='root';
 RETURN NEW;
END $$;
CREATE TRIGGER tr_trace_runs__execution BEFORE INSERT ON trace_runs FOR EACH ROW EXECUTE FUNCTION bind_execution_observation();
CREATE TRIGGER tr_model_snapshots__execution BEFORE INSERT ON model_input_snapshots FOR EACH ROW EXECUTE FUNCTION bind_execution_observation();
ALTER TABLE model_input_snapshots ADD UNIQUE(account_id,snapshot_id);
CREATE TABLE model_dispatches (
 account_id uuid NOT NULL, snapshot_id uuid NOT NULL, dispatched_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,snapshot_id), FOREIGN KEY(account_id,snapshot_id) REFERENCES model_input_snapshots(account_id,snapshot_id)
);
CREATE TRIGGER tr_model_dispatches__immutable BEFORE UPDATE OR DELETE ON model_dispatches FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
GRANT SELECT ON model_dispatches TO hpagent_api;
GRANT SELECT,INSERT ON model_dispatches TO hpagent_worker;

CREATE FUNCTION enforce_artifact_provenance() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.status IN ('running','completed') AND (NEW.producing_run_id IS NULL OR NEW.producing_operation_id IS NULL) THEN
 RAISE EXCEPTION 'artifact requires producing execution/operation'; END IF;
 IF TG_OP='UPDATE' AND OLD.producing_run_id IS NOT NULL AND
 ROW(NEW.producing_run_id,NEW.producing_execution_id,NEW.producing_operation_id,NEW.source_markdown)
 IS DISTINCT FROM ROW(OLD.producing_run_id,OLD.producing_execution_id,OLD.producing_operation_id,OLD.source_markdown) OR
 TG_OP='UPDATE' AND OLD.status='completed' AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'immutable artifact provenance/result'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_artifact_versions__provenance BEFORE INSERT OR UPDATE ON artifact_versions FOR EACH ROW EXECUTE FUNCTION enforce_artifact_provenance();
CREATE OR REPLACE FUNCTION validate_work_requirement() RETURNS trigger LANGUAGE plpgsql AS $$
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
        WHERE e<>ALL(ARRAY['research_report','artifact_version','operation_receipt','delivery_receipt','user_acceptance'])) THEN
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

DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='work_requirements'::regclass AND contype='c'
 AND pg_get_constraintdef(oid) LIKE '%work_json(deliverable_policy,%' LOOP
 EXECUTE format('ALTER TABLE work_requirements DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE work_requirements ADD CHECK(work_json(deliverable_policy,ARRAY['schema_version','required','directory_id','entry_id','operation','notification_target_ref'],4096));
GRANT INSERT ON work_requirements,work_conversations TO hpagent_worker;
ALTER TABLE work_events DROP CONSTRAINT work_events_event_type_check;
ALTER TABLE work_events ADD CHECK(event_type IN
 ('accepted','revised','paused','pause_requested','resumed','stopped','stop_requested','linked','advanced',
  'run_succeeded','run_failed','run_cancelled','completed','result_accepted','budget','input','revoke_input',
  'target','disable_target','accept_result','resolve_delivery','grant_resource','revoke_resource','delivery_accepted','delivery_blocked','artifact_reference'));
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='work_events'::regclass AND contype='c'
 AND pg_get_constraintdef(oid) LIKE '%work_json(bounded_payload,%' LOOP
 EXECUTE format('ALTER TABLE work_events DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE work_events ADD CHECK(work_json(bounded_payload,ARRAY['schema_version','reason','status','conversation_id',
 'run_id','evidence','row_version','control_epoch','limits','budget_version','file_id','source_message_id','purpose','ref_id',
 'artifact_version_id','requirement_revision','target_id','content_scope','delivery_id','outcome','node_id','operations','recursive','grant_id','role'],16384));
ALTER TABLE execution_operations DROP CONSTRAINT execution_operations_operation_type_check;
ALTER TABLE execution_operations ADD CHECK(operation_type IN
 ('context','model','tool','planning','synthesis','result','research_stage','deterministic','artifact_build','notification'));
