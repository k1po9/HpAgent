-- Durable Work Phase 2: Execution owns attempts and data, independently of chat.
-- Target development schema only; never backfill old execution identities.
SET search_path TO hpagent, public;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM runs) THEN
    RAISE EXCEPTION 'Execution decoupling requires an empty development Run store; no historical backfill';
  END IF;
END $$;

ALTER TABLE runs DROP CONSTRAINT fk_runs__sessions;
ALTER TABLE runs ADD CONSTRAINT ck_runs__no_session CHECK(session_id IS NULL);
DROP TABLE sessions;
DROP FUNCTION enforce_session_predecessor_invariants();
DROP FUNCTION validate_session_predecessor(uuid);

CREATE TABLE run_executions (
  execution_id uuid PRIMARY KEY, account_id uuid NOT NULL, run_id uuid NOT NULL,
  role text NOT NULL DEFAULT 'root' CHECK(role='root'),
  parent_execution_id uuid CHECK(parent_execution_id IS NULL),
  branch_key text NOT NULL DEFAULT 'root' CHECK(branch_key='root'),
  attempt_no bigint NOT NULL DEFAULT 1 CHECK(attempt_no>=1),
  status text NOT NULL DEFAULT 'queued' CHECK(status IN
    ('queued','running','cancelling','succeeded','failed','cancelled')),
  context_manifest jsonb NOT NULL CHECK(jsonb_typeof(context_manifest)='object'
    AND context_manifest->>'schema_version'='1' AND octet_length(context_manifest::text)<=16384),
  resource_scope jsonb NOT NULL DEFAULT '{"schema_version":1}' CHECK(
    jsonb_typeof(resource_scope)='object' AND octet_length(resource_scope::text)<=16384),
  result_ref text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id), UNIQUE(account_id,run_id,execution_id),
  FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE CASCADE
);
CREATE FUNCTION create_root_execution() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=hpagent,pg_temp AS $$ BEGIN
  INSERT INTO run_executions(execution_id,account_id,run_id,status,context_manifest,resource_scope)
  VALUES(md5('hpagent:execution:root:'||NEW.run_id::text)::uuid,NEW.account_id,NEW.run_id,NEW.status,
    jsonb_strip_nulls(jsonb_build_object('schema_version',1,'source_kind',NEW.source_kind,
      'work_id',NEW.work_id,'requirement_revision',NEW.requirement_revision,
      'work_control_epoch',NEW.work_control_epoch,'conversation_id',NEW.conversation_id,
      'trigger_message_id',NEW.trigger_message_id,'context_message_seq',NEW.context_message_seq)),
    jsonb_build_object('schema_version',1,'run_snapshot_ref',NEW.run_id));
  RETURN NEW;
END $$;
CREATE TRIGGER tr_runs__root AFTER INSERT ON runs FOR EACH ROW EXECUTE FUNCTION create_root_execution();

ALTER TABLE agent_transcripts DROP CONSTRAINT agent_transcripts_run_id_key;
ALTER TABLE agent_transcripts ADD COLUMN execution_id uuid NOT NULL UNIQUE;
ALTER TABLE agent_transcripts ADD FOREIGN KEY(account_id,run_id,execution_id)
  REFERENCES run_executions(account_id,run_id,execution_id) ON DELETE CASCADE;
ALTER TABLE agent_execution_segments ADD COLUMN execution_id uuid NOT NULL;
ALTER TABLE agent_execution_segments ADD FOREIGN KEY(account_id,run_id,execution_id)
  REFERENCES run_executions(account_id,run_id,execution_id) ON DELETE CASCADE;
ALTER TABLE agent_execution_segments ADD UNIQUE(account_id,run_id,execution_id,segment_id);
ALTER TABLE agent_run_waits ADD COLUMN execution_id uuid NOT NULL;
ALTER TABLE agent_run_waits ADD FOREIGN KEY(account_id,run_id,execution_id)
  REFERENCES run_executions(account_id,run_id,execution_id) ON DELETE CASCADE;

DROP TABLE account_execution_leases;
CREATE TABLE execution_attempt_leases (
  execution_id uuid PRIMARY KEY, account_id uuid NOT NULL, run_id uuid NOT NULL,
  owner_segment_id text, fencing_token bigint NOT NULL DEFAULT 0 CHECK(fencing_token>=0),
  lease_expires_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(account_id,run_id,execution_id) REFERENCES run_executions(account_id,run_id,execution_id)
    ON DELETE CASCADE,
  FOREIGN KEY(account_id,run_id,execution_id,owner_segment_id)
    REFERENCES agent_execution_segments(account_id,run_id,execution_id,segment_id),
  CHECK(owner_segment_id IS NULL OR lease_expires_at IS NOT NULL)
);
CREATE FUNCTION project_execution_status() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=hpagent,pg_temp AS $$ BEGIN
  UPDATE run_executions SET status=NEW.status,updated_at=now() WHERE run_id=NEW.run_id;
  IF NEW.status IN ('cancelling','succeeded','failed','cancelled') THEN
    UPDATE execution_attempt_leases SET owner_segment_id=NULL,lease_expires_at=NULL,
      fencing_token=fencing_token+1,updated_at=now() WHERE run_id=NEW.run_id;
    UPDATE agent_run_waits SET state='cancelled',updated_at=now() WHERE run_id=NEW.run_id AND state='waiting';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_runs__execution_status AFTER UPDATE OF status ON runs
  FOR EACH ROW WHEN (NEW.status IS DISTINCT FROM OLD.status) EXECUTE FUNCTION project_execution_status();
CREATE FUNCTION immutable_execution_identity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF ROW(NEW.execution_id,NEW.account_id,NEW.run_id,NEW.role,NEW.parent_execution_id,
    NEW.branch_key,NEW.context_manifest,NEW.resource_scope) IS DISTINCT FROM
    ROW(OLD.execution_id,OLD.account_id,OLD.run_id,OLD.role,OLD.parent_execution_id,
    OLD.branch_key,OLD.context_manifest,OLD.resource_scope) THEN
    RAISE EXCEPTION 'immutable Execution identity/context'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_execution__identity BEFORE UPDATE ON run_executions
  FOR EACH ROW EXECUTE FUNCTION immutable_execution_identity();

ALTER TABLE workflow_executions ADD COLUMN execution_id uuid NOT NULL;
ALTER TABLE workflow_executions ADD FOREIGN KEY(account_id,run_id,execution_id)
  REFERENCES run_executions(account_id,run_id,execution_id);

ALTER TABLE agent_operations RENAME TO execution_operations;
ALTER TABLE execution_operations ADD COLUMN account_id uuid NOT NULL;
ALTER TABLE execution_operations ADD COLUMN execution_id uuid NOT NULL;
ALTER TABLE execution_operations ADD COLUMN requirement_revision bigint;
ALTER TABLE execution_operations ADD COLUMN work_id uuid;
ALTER TABLE execution_operations ADD COLUMN effect_key varchar(300);
ALTER TABLE execution_operations ADD COLUMN parameters_digest bytea CHECK(
  parameters_digest IS NULL OR octet_length(parameters_digest)=32);
ALTER TABLE execution_operations ADD FOREIGN KEY(account_id,run_id,execution_id)
  REFERENCES run_executions(account_id,run_id,execution_id);
ALTER TABLE execution_operations ADD UNIQUE(account_id,run_id,execution_id,operation_id);
ALTER TABLE execution_operations ADD UNIQUE(account_id,work_id,effect_key);
ALTER TABLE execution_operations ADD CHECK((effect_key IS NULL)=(parameters_digest IS NULL));
ALTER TABLE execution_operations ADD CHECK(effect_key IS NULL OR work_id IS NOT NULL);
ALTER TABLE execution_operations DROP CONSTRAINT ck_agent_operations__type;
ALTER TABLE execution_operations ADD CHECK(operation_type IN
  ('context','model','tool','planning','synthesis','result','research_stage','deterministic'));
CREATE FUNCTION bind_execution_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE; BEGIN
  SELECT * INTO r FROM runs WHERE run_id=NEW.run_id;
  IF TG_OP='INSERT' THEN
    NEW.account_id:=COALESCE(NEW.account_id,r.account_id);
    NEW.execution_id:=COALESCE(NEW.execution_id,(SELECT execution_id FROM run_executions WHERE run_id=r.run_id));
    NEW.work_id:=r.work_id; NEW.requirement_revision:=r.requirement_revision;
  ELSE
    IF ROW(NEW.operation_id,NEW.account_id,NEW.run_id,NEW.execution_id,NEW.operation_type,NEW.work_id,
      NEW.requirement_revision,NEW.effect_key,NEW.parameters_digest) IS DISTINCT FROM
      ROW(OLD.operation_id,OLD.account_id,OLD.run_id,OLD.execution_id,OLD.operation_type,OLD.work_id,
      OLD.requirement_revision,OLD.effect_key,OLD.parameters_digest) OR
      (OLD.status='completed' AND NEW IS DISTINCT FROM OLD) THEN
      RAISE EXCEPTION 'immutable operation provenance/result'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_operations__owner BEFORE INSERT OR UPDATE ON execution_operations
  FOR EACH ROW EXECUTE FUNCTION bind_execution_operation();
CREATE TABLE execution_operation_attempts (
  account_id uuid NOT NULL, run_id uuid NOT NULL, execution_id uuid NOT NULL,
  operation_id text NOT NULL, attempt_no bigint NOT NULL CHECK(attempt_no>=1),
  fencing_token bigint NOT NULL CHECK(fencing_token>=1),
  receipt_ref text NOT NULL, dispatched_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(operation_id,attempt_no), UNIQUE(operation_id,fencing_token),
  FOREIGN KEY(account_id,run_id,execution_id,operation_id)
    REFERENCES execution_operations(account_id,run_id,execution_id,operation_id)
);
CREATE TABLE execution_result_receipts (
  account_id uuid NOT NULL, run_id uuid NOT NULL, execution_id uuid NOT NULL,
  operation_id text NOT NULL, attempt_no bigint NOT NULL, requirement_revision bigint,
  result_ref text NOT NULL, digest bytea NOT NULL CHECK(octet_length(digest)=32),
  disposition text NOT NULL CHECK(disposition IN ('current','stale','cancelled','quarantined')),
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(operation_id,attempt_no),
  FOREIGN KEY(account_id,run_id,execution_id,operation_id)
    REFERENCES execution_operations(account_id,run_id,execution_id,operation_id),
  FOREIGN KEY(operation_id,attempt_no) REFERENCES execution_operation_attempts(operation_id,attempt_no)
);
-- Current receipts are business evidence only while the exact producing
-- attempt still owns progression. Stale receipts never regain that authority.
CREATE FUNCTION validate_execution_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o execution_operations%ROWTYPE; a execution_operation_attempts%ROWTYPE;
  r runs%ROWTYPE; w works%ROWTYPE;
BEGIN
  SELECT * INTO o FROM execution_operations WHERE operation_id=NEW.operation_id;
  SELECT * INTO a FROM execution_operation_attempts WHERE operation_id=NEW.operation_id AND attempt_no=NEW.attempt_no;
  IF NEW.result_ref IS DISTINCT FROM a.receipt_ref OR
    NEW.requirement_revision IS DISTINCT FROM o.requirement_revision THEN
    RAISE EXCEPTION 'receipt does not match registered operation attempt'; END IF;
  IF NEW.disposition='current' THEN
    SELECT * INTO r FROM runs WHERE run_id=NEW.run_id;
    IF r.status NOT IN ('queued','running') OR NOT EXISTS(SELECT 1 FROM accounts
      WHERE account_id=NEW.account_id AND status='active') OR NOT EXISTS(
      SELECT 1 FROM execution_attempt_leases WHERE execution_id=NEW.execution_id
        AND fencing_token=a.fencing_token AND lease_expires_at>clock_timestamp()) THEN
      RAISE EXCEPTION 'current receipt requires a live Execution fence'; END IF;
    IF r.source_kind='work' THEN
      SELECT * INTO w FROM works WHERE work_id=r.work_id;
      IF w.status<>'active' OR w.active_coordinator_run_id IS DISTINCT FROM r.run_id OR
        w.current_requirement_revision<>r.requirement_revision OR w.control_epoch<>r.work_control_epoch THEN
        RAISE EXCEPTION 'receipt cannot satisfy a different requirement/control epoch'; END IF;
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER tr_receipt__intent BEFORE INSERT ON execution_result_receipts
  FOR EACH ROW EXECUTE FUNCTION validate_execution_receipt();
CREATE TRIGGER tr_receipt__immutable BEFORE UPDATE OR DELETE ON execution_result_receipts
  FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
CREATE TRIGGER tr_attempt__immutable BEFORE UPDATE OR DELETE ON execution_operation_attempts
  FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
CREATE TABLE execution_effect_references (
  account_id uuid NOT NULL, run_id uuid NOT NULL, execution_id uuid NOT NULL,
  operation_id text NOT NULL, producing_run_id uuid NOT NULL, producing_execution_id uuid NOT NULL,
  requirement_revision bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(execution_id,operation_id),
  FOREIGN KEY(account_id,run_id,execution_id) REFERENCES run_executions(account_id,run_id,execution_id),
  FOREIGN KEY(account_id,producing_run_id,producing_execution_id,operation_id)
    REFERENCES execution_operations(account_id,run_id,execution_id,operation_id)
);
CREATE TRIGGER tr_effect_reference__immutable BEFORE UPDATE OR DELETE ON execution_effect_references
  FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
ALTER TABLE research_stage_results ADD FOREIGN KEY(operation_id) REFERENCES execution_operations(operation_id);

GRANT SELECT ON run_executions,execution_result_receipts,execution_operations TO hpagent_api;
GRANT SELECT,INSERT,UPDATE ON run_executions,execution_attempt_leases TO hpagent_worker;
GRANT SELECT,INSERT,UPDATE ON execution_operations TO hpagent_worker;
GRANT SELECT,INSERT ON execution_operation_attempts,execution_result_receipts,execution_effect_references TO hpagent_worker;
REVOKE DELETE ON execution_operations FROM hpagent_worker;
REVOKE ALL ON FUNCTION create_root_execution(),project_execution_status() FROM PUBLIC;

CREATE FUNCTION bind_workflow_execution() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  NEW.execution_id:=COALESCE(NEW.execution_id,(SELECT execution_id FROM run_executions
    WHERE account_id=NEW.account_id AND run_id=NEW.run_id AND role='root'));
  RETURN NEW;
END $$;
CREATE TRIGGER tr_workflow__execution BEFORE INSERT ON workflow_executions
  FOR EACH ROW EXECUTE FUNCTION bind_workflow_execution();

CREATE TABLE execution_control_events (
  account_id uuid NOT NULL, run_id uuid NOT NULL, execution_id uuid NOT NULL,
  command_id uuid NOT NULL, action text NOT NULL CHECK(action IN ('cancel','revise','pause','stop')),
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(command_id,execution_id),
  FOREIGN KEY(account_id,run_id,execution_id) REFERENCES run_executions(account_id,run_id,execution_id),
  FOREIGN KEY(account_id,command_id) REFERENCES idempotency_commands(account_id,idempotency_command_id)
);
CREATE TRIGGER tr_control_event__immutable BEFORE UPDATE OR DELETE ON execution_control_events
  FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
GRANT SELECT,INSERT ON execution_control_events TO hpagent_api,hpagent_worker;
CREATE FUNCTION validate_effect_reference() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE; o execution_operations%ROWTYPE;
BEGIN
  SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
  SELECT * INTO o FROM execution_operations WHERE operation_id=NEW.operation_id;
  IF r.work_id IS NULL OR r.work_id IS DISTINCT FROM o.work_id OR
    r.requirement_revision IS DISTINCT FROM NEW.requirement_revision OR o.effect_key IS NULL THEN
    RAISE EXCEPTION 'effect reference must remain inside its Work and fixed requirement'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_effect_reference__owner BEFORE INSERT ON execution_effect_references
  FOR EACH ROW EXECUTE FUNCTION validate_effect_reference();


-- Transcript events cannot borrow an operation from another execution/account.
CREATE FUNCTION validate_transcript_operation_owner() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF NEW.operation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_transcripts t JOIN execution_operations o
      ON o.account_id=t.account_id AND o.run_id=t.run_id AND o.execution_id=t.execution_id
    WHERE t.transcript_id=NEW.transcript_id AND o.operation_id=NEW.operation_id
  ) THEN RAISE EXCEPTION 'transcript operation is outside its Execution'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_transcript_event__operation BEFORE INSERT OR UPDATE ON agent_transcript_events
  FOR EACH ROW EXECUTE FUNCTION validate_transcript_operation_owner();
