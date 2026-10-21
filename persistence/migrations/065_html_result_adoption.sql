-- HTML uses the same local producer identity on Chat and Work. Existing migration bytes stay fixed.
ALTER TABLE runs DROP CONSTRAINT ck_runs__strategy_owner;
ALTER TABLE runs ADD CONSTRAINT ck_runs__strategy_owner CHECK (
 source_kind='chat' AND (strategy_kind='generic_agent' AND executor_key='chat_agent'
   OR strategy_kind='fixed_workflow' AND executor_key='artifact_html') OR
 source_kind='work' AND (strategy_kind='fixed_workflow' AND executor_key IN ('research_report','artifact_html')
   OR strategy_kind='generic_agent' AND executor_key='work_agent'
   OR strategy_kind='deterministic' AND executor_key='reminder'));

CREATE FUNCTION capability_predicate_valid(a uuid, r uuid, p jsonb) RETURNS boolean
LANGUAGE sql STABLE AS $$
 SELECT COALESCE(EXISTS(
 SELECT 1 FROM capability_result_refs f JOIN capability_results cr USING(account_id,invocation_id)
 JOIN runs consumer ON consumer.account_id=f.account_id AND consumer.run_id=r
 LEFT JOIN artifact_versions v ON v.account_id=f.account_id AND v.artifact_version_id=f.artifact_version_id
 JOIN stored_files sf ON sf.account_id=f.account_id AND sf.file_id=COALESCE(f.file_id,v.file_id)
 WHERE f.account_id=a AND f.ref_id=(p->>'subject_ref')::uuid AND sf.status='ready'
 AND (f.artifact_version_id IS NULL OR v.status='completed')
 AND (cr.run_id=r OR EXISTS(SELECT 1 FROM capability_result_adoptions ad
   WHERE ad.account_id=a AND ad.run_id=r AND ad.ref_id=f.ref_id
   AND ad.requirement_revision IS NOT DISTINCT FROM consumer.requirement_revision
   AND ad.work_control_epoch IS NOT DISTINCT FROM consumer.work_control_epoch))
 AND CASE p->>'kind'
 WHEN 'output_exists' THEN f.type IN ('markdown','file','artifact_version')
 WHEN 'check_passed' THEN EXISTS(SELECT 1 FROM capability_result_checks ck
   WHERE ck.account_id=a AND ck.subject_ref=f.ref_id AND ck.verifier_key=p->>'verifier_key'
   AND ck.verifier_version=p->>'verifier_version' AND ck.verdict='passed')
 WHEN 'workspace_saved' THEN EXISTS(
   SELECT 1 FROM workspace_save_operations o JOIN workspace_nodes n USING(account_id,node_id)
   WHERE o.account_id=a AND o.operation_id=p->>'operation_id' AND o.source_run_id=r
   AND o.node_id=(p->>'target_id')::uuid AND n.file_id=sf.file_id AND n.deleted_at IS NULL
   AND p->>'target_revision' IS NULL
   UNION ALL
   SELECT 1 FROM workspace_version_operations o WHERE o.account_id=a
   AND o.operation_id=p->>'operation_id' AND o.source_run_id=r AND o.node_id=(p->>'target_id')::uuid
   AND o.file_id=sf.file_id AND (p->>'target_revision' IS NULL OR o.revision=(p->>'target_revision')::bigint))
 ELSE false END),false)
$$;
CREATE TABLE capability_acceptance_evidence (
 evidence_id uuid PRIMARY KEY, account_id uuid NOT NULL, run_id uuid NOT NULL,
 execution_id uuid NOT NULL, criterion_id text NOT NULL, predicate jsonb NOT NULL,
 subject_ref uuid GENERATED ALWAYS AS ((predicate->>'subject_ref')::uuid) STORED,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(account_id,run_id,execution_id) REFERENCES run_executions(account_id,run_id,execution_id),
 FOREIGN KEY(account_id,subject_ref) REFERENCES capability_result_refs(account_id,ref_id)
);

CREATE FUNCTION validate_capability_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT capability_predicate_valid(NEW.account_id,NEW.run_id,NEW.predicate) OR NOT EXISTS(
 SELECT 1 FROM run_executions e JOIN runs r USING(account_id,run_id)
 WHERE e.account_id=NEW.account_id AND e.run_id=NEW.run_id AND e.execution_id=NEW.execution_id
 AND e.role='root' AND e.status IN ('queued','running') AND r.status='running') THEN
 RAISE EXCEPTION 'typed acceptance requires current parent and exact evidence'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_acceptance BEFORE INSERT ON capability_acceptance_evidence
 FOR EACH ROW EXECUTE FUNCTION validate_capability_acceptance();
CREATE TRIGGER tr_capability_acceptance_immutable BEFORE UPDATE OR DELETE ON capability_acceptance_evidence
 FOR EACH ROW EXECUTE FUNCTION immutable_work_fact();
GRANT SELECT ON capability_acceptance_evidence TO hpagent_api;
GRANT SELECT,INSERT ON capability_acceptance_evidence TO hpagent_worker;

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
        WHERE e<>ALL(ARRAY['research_report','artifact_version','operation_receipt','delivery_receipt','user_acceptance','output_exists','check_passed','workspace_saved'])) THEN
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
    WHERE e->>'type' IN ('output_exists','check_passed','workspace_saved') AND NOT EXISTS(
      SELECT 1 FROM capability_acceptance_evidence ce WHERE ce.account_id=NEW.account_id
      AND ce.run_id=r.run_id AND ce.criterion_id=e->>'criterion_id' AND ce.evidence_id=(e->>'ref')::uuid
      AND ce.predicate->>'kind'=e->>'type' AND capability_predicate_valid(NEW.account_id,r.run_id,ce.predicate))) THEN
    RAISE EXCEPTION 'typed evidence mismatch'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(receipt->'evidence') e
    WHERE e->>'type'='research_report' AND NOT EXISTS(
      SELECT 1 FROM artifact_versions v JOIN artifacts a ON a.artifact_id=v.artifact_id
      WHERE v.artifact_version_id=(e->>'ref')::uuid AND v.account_id=r.account_id
        AND a.account_id=r.account_id AND v.producing_run_id=r.run_id
        AND v.status='completed') AND NOT EXISTS(SELECT 1 FROM research_reports rr JOIN capability_results cr USING(invocation_id) JOIN stored_files sf ON sf.file_id=rr.markdown_file_id AND sf.account_id=cr.account_id WHERE rr.run_id=r.run_id AND cr.account_id=r.account_id AND rr.markdown_file_id=(e->>'ref')::uuid AND cr.payload->>'outcome'='succeeded' AND sf.status='ready' AND EXISTS(SELECT 1 FROM capability_result_checks ck WHERE ck.invocation_id=cr.invocation_id AND ck.verifier_key='research.basic' AND ck.verifier_version='1' AND ck.verdict='passed'))) THEN
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


CREATE FUNCTION enforce_generic_typed_result() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.executor_key='work_agent' AND NEW.status='succeeded' AND EXISTS(
 SELECT 1 FROM jsonb_array_elements(NEW.result_json->'evidence') e
 WHERE e->>'type' NOT IN ('output_exists','check_passed','workspace_saved')) THEN
 RAISE EXCEPTION 'Generic completion requires typed result evidence'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_generic_typed_result BEFORE INSERT OR UPDATE ON runs
 FOR EACH ROW EXECUTE FUNCTION enforce_generic_typed_result();
