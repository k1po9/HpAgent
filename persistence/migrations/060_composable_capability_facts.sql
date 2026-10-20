-- S1: immutable capability description/results, no new invocation state machine.
SET search_path TO hpagent, public;
ALTER TABLE run_executions DROP CONSTRAINT run_executions_check;
ALTER TABLE run_executions ADD COLUMN invocation_id text;
ALTER TABLE run_executions ADD CONSTRAINT ck_execution_role CHECK(
 (role='root' AND parent_execution_id IS NULL AND branch_key='root' AND branch_attempt=1 AND invocation_id IS NULL)
 OR (role='subagent' AND parent_execution_id IS NOT NULL AND branch_key ~ '^[a-z][a-z0-9_-]{0,39}$' AND invocation_id IS NULL)
 OR (role='capability' AND parent_execution_id IS NOT NULL AND branch_key='capability' AND branch_attempt=1 AND invocation_id IS NOT NULL));
DROP TRIGGER tr_execution_subagent ON run_executions;
CREATE TRIGGER tr_execution_subagent BEFORE INSERT ON run_executions
 FOR EACH ROW WHEN (NEW.role='subagent') EXECUTE FUNCTION validate_subagent();
CREATE TABLE capability_invocations (
 invocation_id text PRIMARY KEY, schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
 account_id uuid NOT NULL, run_id uuid NOT NULL, caller_execution_id uuid NOT NULL,
 child_execution_id uuid NOT NULL UNIQUE,
 capability_key varchar(80) NOT NULL, contract_version integer NOT NULL CHECK(contract_version>=1),
 implementation_version varchar(80) NOT NULL,
 descriptor jsonb NOT NULL CHECK(jsonb_typeof(descriptor)='object' AND descriptor->>'schema_version'='1'
   AND octet_length(descriptor::text)<=65536),
 descriptor_digest bytea NOT NULL CHECK(octet_length(descriptor_digest)=32),
 requirement_revision bigint, work_control_epoch bigint,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(account_id,run_id,child_execution_id,invocation_id),
 UNIQUE(account_id,invocation_id),
 FOREIGN KEY(account_id,run_id,caller_execution_id,invocation_id)
   REFERENCES execution_operations(account_id,run_id,execution_id,operation_id),
 FOREIGN KEY(account_id,run_id,child_execution_id)
   REFERENCES run_executions(account_id,run_id,execution_id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE run_executions ADD FOREIGN KEY(account_id,run_id,execution_id,invocation_id)
 REFERENCES capability_invocations(account_id,run_id,child_execution_id,invocation_id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE FUNCTION validate_capability_invocation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p run_executions%ROWTYPE; r runs%ROWTYPE; o execution_operations%ROWTYPE;
BEGIN
 SELECT * INTO p FROM run_executions WHERE account_id=NEW.account_id AND run_id=NEW.run_id
   AND execution_id=NEW.caller_execution_id FOR UPDATE;
 SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
 SELECT * INTO o FROM execution_operations WHERE operation_id=NEW.invocation_id;
 IF p.role IS DISTINCT FROM 'root' OR p.status NOT IN ('queued','running')
   OR r.status NOT IN ('queued','running') OR o.operation_type IS DISTINCT FROM 'tool'
   OR o.status NOT IN ('started','intent_recorded')
   OR NEW.capability_key IS DISTINCT FROM NEW.descriptor->'definition'->>'key'
   OR NEW.contract_version IS DISTINCT FROM (NEW.descriptor->'definition'->>'contract_version')::integer
   OR NEW.implementation_version IS DISTINCT FROM NEW.descriptor->'definition'->>'implementation_version'
   OR NEW.requirement_revision IS DISTINCT FROM r.requirement_revision
   OR NEW.work_control_epoch IS DISTINCT FROM r.work_control_epoch THEN
   RAISE EXCEPTION 'capability invocation requires current root tool operation'; END IF;
 IF r.source_kind='work' AND NOT EXISTS(SELECT 1 FROM works w WHERE w.account_id=r.account_id
   AND w.work_id=r.work_id AND w.status='active' AND w.active_coordinator_run_id=r.run_id
   AND w.current_requirement_revision=r.requirement_revision AND w.control_epoch=r.work_control_epoch) THEN
   RAISE EXCEPTION 'capability invocation requires current Work ownership'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_invocation_owner BEFORE INSERT ON capability_invocations
 FOR EACH ROW EXECUTE FUNCTION validate_capability_invocation();
CREATE FUNCTION validate_capability_execution() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM capability_invocations i WHERE i.account_id=NEW.account_id
   AND i.run_id=NEW.run_id AND i.child_execution_id=NEW.execution_id AND i.invocation_id=NEW.invocation_id
   AND i.caller_execution_id=NEW.parent_execution_id
   AND NEW.resource_scope=i.descriptor->'resource_scope'
   AND NEW.context_manifest=jsonb_build_object('schema_version',1,'invocation_id',i.invocation_id)) THEN
   RAISE EXCEPTION 'capability Execution must match its immutable invocation'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_execution BEFORE INSERT ON run_executions
 FOR EACH ROW WHEN (NEW.role='capability') EXECUTE FUNCTION validate_capability_execution();
CREATE OR REPLACE FUNCTION immutable_execution_identity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF ROW(NEW.execution_id,NEW.account_id,NEW.run_id,NEW.role,NEW.parent_execution_id,NEW.branch_key,
    NEW.branch_attempt,NEW.invocation_id,NEW.context_manifest,NEW.resource_scope) IS DISTINCT FROM
    ROW(OLD.execution_id,OLD.account_id,OLD.run_id,OLD.role,OLD.parent_execution_id,OLD.branch_key,
    OLD.branch_attempt,OLD.invocation_id,OLD.context_manifest,OLD.resource_scope) THEN
  RAISE EXCEPTION 'immutable Execution identity/context'; END IF;
 IF OLD.role IN ('subagent','capability') AND OLD.status IN ('succeeded','failed','cancelled') AND NEW IS DISTINCT FROM OLD THEN
  RAISE EXCEPTION 'immutable terminal branch'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION project_execution_status() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=hpagent,pg_temp AS $$ BEGIN
 UPDATE run_executions SET status=NEW.status,updated_at=now() WHERE run_id=NEW.run_id AND role='root';
 IF NEW.status IN ('cancelling','succeeded','failed','cancelled') THEN
  UPDATE run_executions SET status=CASE WHEN NEW.status='cancelling' THEN 'cancelling' ELSE 'cancelled' END,
    updated_at=now() WHERE run_id=NEW.run_id AND role IN ('subagent','capability') AND status IN ('queued','running','cancelling');
  UPDATE execution_attempt_leases SET owner_segment_id=NULL,lease_expires_at=NULL,
    fencing_token=fencing_token+1,updated_at=now() WHERE run_id=NEW.run_id;
  UPDATE agent_run_waits SET state='cancelled',updated_at=now() WHERE run_id=NEW.run_id AND state='waiting';
 END IF;
 RETURN NEW;
END $$;
CREATE TABLE capability_results (
 invocation_id text PRIMARY KEY, account_id uuid NOT NULL, run_id uuid NOT NULL,
 producing_execution_id uuid NOT NULL, producing_operation_id text NOT NULL UNIQUE,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND payload->>'schema_version'='1'
   AND payload->>'outcome' IN ('succeeded','insufficient','failed','cancelled','uncertain')
   AND octet_length(payload::text)<=65536),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(account_id,invocation_id),
 FOREIGN KEY(account_id,run_id,producing_execution_id,invocation_id)
   REFERENCES capability_invocations(account_id,run_id,child_execution_id,invocation_id),
 FOREIGN KEY(account_id,run_id,producing_execution_id,producing_operation_id)
   REFERENCES execution_operations(account_id,run_id,execution_id,operation_id)
);
CREATE FUNCTION validate_capability_result() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM execution_operations o WHERE o.operation_id=NEW.producing_operation_id
   AND o.status='completed' AND o.result_payload=NEW.payload
   AND NEW.payload->'provenance'=jsonb_build_object('schema_version',1,
     'invocation_id',NEW.invocation_id,'execution_id',NEW.producing_execution_id,
     'operation_id',NEW.producing_operation_id)) THEN
   RAISE EXCEPTION 'capability result requires committed producer operation'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_result BEFORE INSERT ON capability_results
 FOR EACH ROW EXECUTE FUNCTION validate_capability_result();
CREATE TABLE capability_result_refs (
 ref_id uuid PRIMARY KEY, schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
 account_id uuid NOT NULL, invocation_id text NOT NULL,
 type text NOT NULL CHECK(type IN ('markdown','file','check_evidence','artifact_version')),
 file_id uuid, artifact_id uuid, artifact_version_id uuid, sha256 char(64),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(account_id,ref_id),
 FOREIGN KEY(account_id,invocation_id) REFERENCES capability_results(account_id,invocation_id),
 FOREIGN KEY(account_id,file_id) REFERENCES stored_files(account_id,file_id),
 FOREIGN KEY(account_id,artifact_id,artifact_version_id)
   REFERENCES artifact_versions(account_id,artifact_id,artifact_version_id),
 CHECK((type='artifact_version' AND artifact_id IS NOT NULL AND artifact_version_id IS NOT NULL AND file_id IS NULL)
   OR (type<>'artifact_version' AND file_id IS NOT NULL AND artifact_id IS NULL AND artifact_version_id IS NULL)),
 CHECK(sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$')
);
CREATE INDEX ix_capability_ref_file ON capability_result_refs(account_id,file_id);
CREATE FUNCTION validate_capability_ref() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE payload jsonb; ref jsonb; f stored_files%ROWTYPE; producer capability_results%ROWTYPE;
BEGIN
 SELECT r.* INTO producer FROM capability_results r WHERE r.account_id=NEW.account_id
   AND r.invocation_id=NEW.invocation_id;
 payload:=producer.payload;
 SELECT e INTO ref FROM jsonb_array_elements(payload->'outputs') e WHERE e->>'ref_id'=NEW.ref_id::text;
 IF ref IS NULL OR ref->>'schema_version' IS DISTINCT FROM '1'
   OR ref->>'producing_invocation_id' IS DISTINCT FROM NEW.invocation_id
   OR ref->>'producing_execution_id' IS DISTINCT FROM producer.producing_execution_id::text
   OR ref->>'producing_operation_id' IS DISTINCT FROM producer.producing_operation_id
   OR ref->>'type' IS DISTINCT FROM NEW.type
   OR ref->>'object_id' IS DISTINCT FROM COALESCE(NEW.file_id,NEW.artifact_id)::text
   OR ref->>'version' IS DISTINCT FROM COALESCE(NEW.file_id,NEW.artifact_version_id)::text
   OR ref->>'sha256' IS DISTINCT FROM NEW.sha256 THEN
   RAISE EXCEPTION 'result reference does not match committed payload'; END IF;
 IF NEW.file_id IS NOT NULL THEN
   SELECT * INTO f FROM stored_files WHERE account_id=NEW.account_id AND file_id=NEW.file_id FOR UPDATE;
   IF f.status IS DISTINCT FROM 'ready' OR f.purpose IS DISTINCT FROM 'output'
     OR f.source_run_id IS DISTINCT FROM producer.run_id OR (NEW.sha256 IS NOT NULL AND NEW.sha256<>f.sha256) THEN
     RAISE EXCEPTION 'result file is unavailable or digest differs'; END IF;
 ELSE
   IF NOT EXISTS(SELECT 1 FROM artifact_versions WHERE account_id=NEW.account_id
     AND artifact_version_id=NEW.artifact_version_id AND producing_run_id=producer.run_id
     AND producing_execution_id=producer.producing_execution_id AND status='completed' FOR SHARE) THEN
     RAISE EXCEPTION 'result artifact version is unavailable'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_ref BEFORE INSERT ON capability_result_refs
 FOR EACH ROW EXECUTE FUNCTION validate_capability_ref();
CREATE TABLE capability_result_checks (
 account_id uuid NOT NULL, invocation_id text NOT NULL,
 verifier_key varchar(80) NOT NULL, verifier_version varchar(80) NOT NULL,
 subject_ref uuid NOT NULL, evidence_ref uuid NOT NULL,
 verdict text NOT NULL CHECK(verdict IN ('passed','failed','not_checked')),
 PRIMARY KEY(invocation_id,verifier_key,verifier_version,subject_ref),
 FOREIGN KEY(account_id,invocation_id) REFERENCES capability_results(account_id,invocation_id),
 FOREIGN KEY(account_id,subject_ref) REFERENCES capability_result_refs(account_id,ref_id),
 FOREIGN KEY(account_id,evidence_ref) REFERENCES capability_result_refs(account_id,ref_id)
);
CREATE TABLE capability_input_refs (
 account_id uuid NOT NULL, invocation_id text NOT NULL, ref_id uuid NOT NULL,
 PRIMARY KEY(invocation_id,ref_id),
 FOREIGN KEY(account_id,invocation_id) REFERENCES capability_invocations(account_id,invocation_id),
 FOREIGN KEY(account_id,ref_id) REFERENCES capability_result_refs(account_id,ref_id)
);
CREATE TABLE capability_result_adoptions (
 schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
 account_id uuid NOT NULL, run_id uuid NOT NULL, execution_id uuid NOT NULL, ref_id uuid NOT NULL,
 requirement_revision bigint, work_control_epoch bigint,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(execution_id,ref_id),
 FOREIGN KEY(account_id,run_id,execution_id) REFERENCES run_executions(account_id,run_id,execution_id),
 FOREIGN KEY(account_id,ref_id) REFERENCES capability_result_refs(account_id,ref_id)
);
CREATE FUNCTION validate_capability_adoption() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM runs r JOIN run_executions e USING(account_id,run_id)
   WHERE r.account_id=NEW.account_id AND r.run_id=NEW.run_id AND e.execution_id=NEW.execution_id
   AND e.role='root' AND e.status IN ('queued','running') AND r.status IN ('queued','running')
   AND r.requirement_revision IS NOT DISTINCT FROM NEW.requirement_revision
   AND r.work_control_epoch IS NOT DISTINCT FROM NEW.work_control_epoch
   AND (r.source_kind='chat' OR EXISTS(SELECT 1 FROM works w WHERE w.account_id=r.account_id
     AND w.work_id=r.work_id AND w.status='active' AND w.active_coordinator_run_id=r.run_id
     AND w.current_requirement_revision=r.requirement_revision AND w.control_epoch=r.work_control_epoch))) THEN
   RAISE EXCEPTION 'adoption must bind current root revision and epoch'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_adoption BEFORE INSERT ON capability_result_adoptions
 FOR EACH ROW EXECUTE FUNCTION validate_capability_adoption();
CREATE TABLE capability_observations (
 schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
 invocation_id text PRIMARY KEY, account_id uuid NOT NULL, run_id uuid NOT NULL,
 caller_execution_id uuid NOT NULL, producing_operation_id text NOT NULL,
 transcript_id text NOT NULL, sequence bigint NOT NULL,
 FOREIGN KEY(account_id,run_id,caller_execution_id,invocation_id)
   REFERENCES execution_operations(account_id,run_id,execution_id,operation_id),
 FOREIGN KEY(account_id,invocation_id) REFERENCES capability_results(account_id,invocation_id),
 FOREIGN KEY(transcript_id,sequence) REFERENCES agent_transcript_events(transcript_id,sequence)
);
CREATE FUNCTION validate_capability_observation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM capability_invocations i JOIN capability_results r USING(invocation_id)
   JOIN execution_operations o ON o.operation_id=i.invocation_id
   JOIN agent_transcript_events e ON e.transcript_id=NEW.transcript_id AND e.sequence=NEW.sequence
   WHERE i.invocation_id=NEW.invocation_id AND i.account_id=NEW.account_id AND i.run_id=NEW.run_id
   AND i.caller_execution_id=NEW.caller_execution_id AND r.producing_operation_id=NEW.producing_operation_id
   AND o.status='completed' AND e.operation_id=i.invocation_id AND e.event_type='tool_result'
   AND o.result_payload->'capability_observation'=jsonb_build_object('schema_version',1,
     'invocation_id',i.invocation_id,'producing_operation_id',r.producing_operation_id,
     'producer_result_ref',(SELECT result_ref FROM execution_operations WHERE operation_id=r.producing_operation_id))
   AND e.payload->'capability_observation'=o.result_payload->'capability_observation') THEN
   RAISE EXCEPTION 'observation must link committed caller event and exact producer'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_observation BEFORE INSERT ON capability_observations
 FOR EACH ROW EXECUTE FUNCTION validate_capability_observation();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['capability_invocations','capability_results','capability_result_refs',
   'capability_result_checks','capability_input_refs','capability_result_adoptions','capability_observations'] LOOP
   EXECUTE format('CREATE TRIGGER tr_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_work_fact()',t);
   EXECUTE format('GRANT SELECT ON %I TO hpagent_api',t);
   EXECUTE format('GRANT SELECT,INSERT ON %I TO hpagent_worker',t);
 END LOOP;
END $$;

-- All result JSON edges must have durable rows, including failed/unobserved calls.
CREATE FUNCTION validate_capability_result_edges() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
 SELECT count(*) INTO n FROM capability_result_refs WHERE invocation_id=NEW.invocation_id;
 IF n<>jsonb_array_length(NEW.payload->'outputs') THEN
   RAISE EXCEPTION 'result outputs must all have retained references'; END IF;
 SELECT count(*) INTO n FROM capability_result_checks WHERE invocation_id=NEW.invocation_id;
 IF n<>jsonb_array_length(NEW.payload->'checks') THEN
   RAISE EXCEPTION 'result checks must all have durable evidence edges'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER tr_capability_result_edges AFTER INSERT ON capability_results
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_capability_result_edges();
CREATE FUNCTION validate_capability_check() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM capability_results r, jsonb_array_elements(r.payload->'checks') c
   WHERE r.account_id=NEW.account_id AND r.invocation_id=NEW.invocation_id
   AND c=jsonb_build_object('schema_version',1,'verifier_key',NEW.verifier_key,
     'verifier_version',NEW.verifier_version,'subject_ref',NEW.subject_ref,'evidence_ref',NEW.evidence_ref,
     'verdict',NEW.verdict)) OR NOT EXISTS(SELECT 1 FROM capability_result_refs s
     JOIN capability_result_refs e USING(account_id,invocation_id) WHERE s.account_id=NEW.account_id
     AND s.invocation_id=NEW.invocation_id AND s.ref_id=NEW.subject_ref AND e.ref_id=NEW.evidence_ref
     AND e.type='check_evidence') THEN
   RAISE EXCEPTION 'check must match committed result and exact evidence'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_check BEFORE INSERT ON capability_result_checks
 FOR EACH ROW EXECUTE FUNCTION validate_capability_check();
CREATE FUNCTION validate_capability_input() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM capability_invocations i WHERE i.account_id=NEW.account_id
   AND i.invocation_id=NEW.invocation_id AND i.descriptor->'resource_scope'->'result_ids' ? NEW.ref_id::text) THEN
   RAISE EXCEPTION 'input reference must be explicitly frozen in invocation scope'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_input BEFORE INSERT ON capability_input_refs
 FOR EACH ROW EXECUTE FUNCTION validate_capability_input();
