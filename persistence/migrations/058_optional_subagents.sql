-- Durable Work Phase 5. One root, depth one, three logical branches, two tries.
SET search_path TO hpagent, public;
ALTER TABLE run_executions DROP CONSTRAINT run_executions_role_check;
ALTER TABLE run_executions DROP CONSTRAINT run_executions_parent_execution_id_check;
ALTER TABLE run_executions DROP CONSTRAINT run_executions_branch_key_check;
ALTER TABLE run_executions DROP CONSTRAINT run_executions_run_id_key;
ALTER TABLE run_executions ADD COLUMN branch_attempt integer NOT NULL DEFAULT 1 CHECK(branch_attempt BETWEEN 1 AND 2);
ALTER TABLE run_executions ADD COLUMN error_code text;
ALTER TABLE run_executions ADD CHECK((role='root' AND parent_execution_id IS NULL AND branch_key='root' AND branch_attempt=1)
 OR (role='subagent' AND parent_execution_id IS NOT NULL AND branch_key ~ '^[a-z][a-z0-9_-]{0,39}$'));
ALTER TABLE run_executions ADD FOREIGN KEY(account_id,run_id,parent_execution_id)
 REFERENCES run_executions(account_id,run_id,execution_id);
CREATE UNIQUE INDEX uq_execution_root ON run_executions(run_id) WHERE role='root';
CREATE UNIQUE INDEX uq_execution_branch_attempt ON run_executions(parent_execution_id,branch_key,branch_attempt) WHERE role='subagent';
CREATE INDEX ix_execution_children ON run_executions(account_id,run_id,parent_execution_id,status);

CREATE TABLE execution_delegations (
 account_id uuid NOT NULL, run_id uuid NOT NULL, execution_id uuid PRIMARY KEY,
 operation_id text NOT NULL UNIQUE, briefs jsonb NOT NULL CHECK(jsonb_typeof(briefs)='array'
   AND jsonb_array_length(briefs) BETWEEN 1 AND 3 AND octet_length(briefs::text)<=12288),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(account_id,run_id,execution_id,operation_id)
 REFERENCES execution_operations(account_id,run_id,execution_id,operation_id)
);
CREATE TRIGGER tr_delegation_immutable BEFORE UPDATE OR DELETE ON execution_delegations
 FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
GRANT SELECT,INSERT ON execution_delegations TO hpagent_worker;
GRANT SELECT ON execution_delegations TO hpagent_api;

CREATE FUNCTION validate_subagent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p run_executions%ROWTYPE; r runs%ROWTYPE; w works%ROWTYPE; b jsonb;
BEGIN
 IF NEW.role='root' THEN RETURN NEW; END IF;
 SELECT * INTO p FROM run_executions WHERE account_id=NEW.account_id AND run_id=NEW.run_id
  AND execution_id=NEW.parent_execution_id FOR UPDATE;
 SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
 SELECT * INTO w FROM works WHERE account_id=NEW.account_id AND work_id=r.work_id;
 IF p.role IS DISTINCT FROM 'root' OR p.status NOT IN ('queued','running') OR r.executor_key<>'work_agent'
  OR r.source_kind<>'work' OR r.status NOT IN ('queued','running') OR w.status<>'active'
  OR w.active_coordinator_run_id IS DISTINCT FROM r.run_id OR w.control_epoch<>r.work_control_epoch
  OR w.current_requirement_revision<>r.requirement_revision THEN
  RAISE EXCEPTION 'subagent requires current Generic Work root ownership'; END IF;
 SELECT brief INTO b FROM execution_delegations d, jsonb_array_elements(d.briefs) brief
  WHERE d.execution_id=p.execution_id AND brief->>'branch_key'=NEW.branch_key;
 IF b IS NULL OR NEW.context_manifest->'brief' IS DISTINCT FROM b
  OR NEW.resource_scope IS DISTINCT FROM jsonb_build_object('schema_version',1,
    'node_ids',b->'node_ids','file_ids',b->'file_ids','tool_names',b->'tool_names')
  OR NEW.context_manifest->>'work_id' IS DISTINCT FROM r.work_id::text
  OR (NEW.context_manifest->>'requirement_revision')::bigint IS DISTINCT FROM r.requirement_revision
  OR (NEW.context_manifest->>'work_control_epoch')::bigint IS DISTINCT FROM r.work_control_epoch THEN
  RAISE EXCEPTION 'child brief/scope must match its registered delegation'; END IF;
 IF NEW.branch_attempt=2 AND NOT EXISTS(SELECT 1 FROM run_executions WHERE
  parent_execution_id=p.execution_id AND branch_key=NEW.branch_key AND branch_attempt=1 AND status='failed') THEN
  RAISE EXCEPTION 'retry requires failed previous branch'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_execution_subagent BEFORE INSERT ON run_executions
 FOR EACH ROW EXECUTE FUNCTION validate_subagent();
CREATE OR REPLACE FUNCTION immutable_execution_identity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF ROW(NEW.execution_id,NEW.account_id,NEW.run_id,NEW.role,NEW.parent_execution_id,NEW.branch_key,
    NEW.branch_attempt,NEW.context_manifest,NEW.resource_scope) IS DISTINCT FROM
    ROW(OLD.execution_id,OLD.account_id,OLD.run_id,OLD.role,OLD.parent_execution_id,OLD.branch_key,
    OLD.branch_attempt,OLD.context_manifest,OLD.resource_scope) THEN
  RAISE EXCEPTION 'immutable Execution identity/context'; END IF;
 IF OLD.role='subagent' AND OLD.status IN ('succeeded','failed','cancelled') AND NEW IS DISTINCT FROM OLD THEN
  RAISE EXCEPTION 'immutable terminal branch'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION project_execution_status() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=hpagent,pg_temp AS $$ BEGIN
 UPDATE run_executions SET status=NEW.status,updated_at=now() WHERE run_id=NEW.run_id AND role='root';
 IF NEW.status IN ('cancelling','succeeded','failed','cancelled') THEN
  UPDATE run_executions SET status=CASE WHEN NEW.status='cancelling' THEN 'cancelling' ELSE 'cancelled' END,
    updated_at=now() WHERE run_id=NEW.run_id AND role='subagent' AND status IN ('queued','running','cancelling');
  UPDATE execution_attempt_leases SET owner_segment_id=NULL,lease_expires_at=NULL,
    fencing_token=fencing_token+1,updated_at=now() WHERE run_id=NEW.run_id;
  UPDATE agent_run_waits SET state='cancelled',updated_at=now() WHERE run_id=NEW.run_id AND state='waiting';
 END IF;
 RETURN NEW;
END $$;
-- Root fallback is only for fixed executors; Agent operations pass their Execution explicitly.
CREATE OR REPLACE FUNCTION bind_execution_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE; BEGIN
 SELECT * INTO r FROM runs WHERE run_id=NEW.run_id;
 IF TG_OP='INSERT' THEN
  NEW.account_id:=COALESCE(NEW.account_id,r.account_id);
  NEW.execution_id:=COALESCE(NEW.execution_id,(SELECT execution_id FROM run_executions
    WHERE run_id=r.run_id AND role='root'));
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
ALTER TABLE run_executions ADD UNIQUE(run_id,execution_id);
-- Attribute actual reservations without opening an Execution budget account.
ALTER TABLE run_usage_ledger ADD COLUMN execution_id uuid;
ALTER TABLE run_usage_ledger ADD FOREIGN KEY(run_id,execution_id) REFERENCES run_executions(run_id,execution_id);

CREATE INDEX ix_usage_execution ON run_usage_ledger(run_id,execution_id);
CREATE OR REPLACE FUNCTION bind_execution_observation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 SELECT r.work_id,r.requirement_revision INTO NEW.work_id,NEW.requirement_revision
 FROM runs r WHERE r.account_id=NEW.account_id AND r.run_id=NEW.run_id;
 NEW.execution_id:=COALESCE(NEW.execution_id,(SELECT execution_id FROM run_executions
   WHERE account_id=NEW.account_id AND run_id=NEW.run_id AND role='root'));
 RETURN NEW;
END $$;
CREATE FUNCTION guard_work_branch_completion() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.status='completed' AND OLD.status<>'completed' AND EXISTS(
   SELECT 1 FROM execution_delegations d, jsonb_array_elements(d.briefs) b
   WHERE d.account_id=NEW.account_id AND d.run_id=(NEW.completion_receipt->>'run_id')::uuid
   AND (b->>'required')::boolean AND NOT EXISTS(SELECT 1 FROM run_executions e
     WHERE e.parent_execution_id=d.execution_id AND e.branch_key=b->>'branch_key' AND e.status='succeeded')
 ) THEN RAISE EXCEPTION 'required branch result missing'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_work_required_branches BEFORE UPDATE ON works
 FOR EACH ROW EXECUTE FUNCTION guard_work_branch_completion();
