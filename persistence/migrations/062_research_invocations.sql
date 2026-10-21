-- Research instance identity; NULL invocation retains pre-capability historical rows only.
SET search_path TO hpagent, public;
ALTER TABLE capability_invocations ADD UNIQUE(account_id,run_id,invocation_id);
ALTER TABLE research_plans ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE research_plans ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE source_records ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE source_records ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE evidence_items ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE evidence_items ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE research_stage_results ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE research_stage_results ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE research_iterations ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE research_iterations ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE research_claims ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE research_claims ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE research_citations ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE research_citations ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE research_reports ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE research_reports ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE research_history_baselines ADD COLUMN invocation_id text REFERENCES capability_invocations(invocation_id);
ALTER TABLE research_history_baselines ADD COLUMN research_id text GENERATED ALWAYS AS (COALESCE(invocation_id,run_id::text)) STORED;
ALTER TABLE research_plans DROP CONSTRAINT research_plans_pkey;
ALTER TABLE research_plans ADD PRIMARY KEY(research_id);
ALTER TABLE research_reports DROP CONSTRAINT research_reports_pkey;
ALTER TABLE research_reports ADD PRIMARY KEY(research_id);
ALTER TABLE research_history_baselines DROP CONSTRAINT research_history_baselines_pkey;
ALTER TABLE research_history_baselines ADD PRIMARY KEY(research_id);
ALTER TABLE source_records DROP CONSTRAINT uq_source_records__run_uri;
ALTER TABLE source_records ADD UNIQUE(research_id,canonical_uri);
ALTER TABLE evidence_items DROP CONSTRAINT uq_evidence_items__source;
ALTER TABLE evidence_items ADD UNIQUE(research_id,source_id);
ALTER TABLE research_iterations DROP CONSTRAINT research_iterations_pkey;
ALTER TABLE research_iterations ADD PRIMARY KEY(research_id,iteration);
ALTER TABLE research_claims DROP CONSTRAINT uq_research_claims__ordinal;
ALTER TABLE research_claims ADD UNIQUE(research_id,ordinal);
ALTER TABLE research_reports ADD COLUMN markdown_file_id uuid REFERENCES stored_files(file_id);
ALTER TABLE research_reports ADD COLUMN previous_invocation_id text REFERENCES capability_invocations(invocation_id);
-- Chat invocations have no Work requirement. Run remains the attribution/budget owner.
ALTER TABLE research_plans ALTER COLUMN work_id DROP NOT NULL;
ALTER TABLE research_plans ALTER COLUMN requirement_revision DROP NOT NULL;
ALTER TABLE source_records ALTER COLUMN work_id DROP NOT NULL;
ALTER TABLE source_records ALTER COLUMN requirement_revision DROP NOT NULL;
CREATE FUNCTION validate_research_instance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE i capability_invocations%ROWTYPE;
BEGIN
 IF NEW.invocation_id IS NOT NULL THEN
  SELECT * INTO i FROM capability_invocations WHERE invocation_id=NEW.invocation_id;
  IF NOT FOUND OR i.run_id<>NEW.run_id OR i.capability_key<>'research' THEN
   RAISE EXCEPTION 'Research invocation owner mismatch'; END IF;
 END IF;
 IF TG_TABLE_NAME='evidence_items' THEN
 IF NOT EXISTS(SELECT 1 FROM source_records s
   WHERE s.source_id=NEW.source_id AND s.run_id=NEW.run_id AND s.invocation_id IS NOT DISTINCT FROM NEW.invocation_id) THEN
  RAISE EXCEPTION 'Research evidence source mismatch'; END IF;
 END IF;
 IF TG_TABLE_NAME='research_citations' THEN
  IF NOT EXISTS(SELECT 1 FROM research_claims c WHERE c.claim_id=NEW.claim_id
     AND c.run_id=NEW.run_id AND c.invocation_id IS NOT DISTINCT FROM NEW.invocation_id)
   OR NOT EXISTS(SELECT 1 FROM evidence_items e WHERE e.evidence_id=NEW.evidence_id
     AND e.run_id=NEW.run_id AND e.invocation_id IS NOT DISTINCT FROM NEW.invocation_id) THEN
   RAISE EXCEPTION 'Research citation instance mismatch'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON research_plans FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON source_records FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON evidence_items FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON research_stage_results FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON research_iterations FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON research_claims FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON research_citations FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON research_reports FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
CREATE TRIGGER tr_research_instance BEFORE INSERT OR UPDATE ON research_history_baselines FOR EACH ROW EXECUTE FUNCTION validate_research_instance();
ALTER TABLE research_stage_results DROP CONSTRAINT uq_research_stage_results__run_stage;
ALTER TABLE research_stage_results ADD UNIQUE(research_id,stage);

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

-- Index exactly the committed immutable Markdown, never the first report of its Run.
CREATE FUNCTION index_invocation_research_summary() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$ BEGIN
 IF NEW.markdown_file_id IS NOT NULL THEN
  INSERT INTO workspace_file_summaries(account_id,file_id,sha256,summary,source_run_id)
  SELECT sf.account_id,sf.file_id,sf.sha256,left(NEW.report_markdown,500),NEW.run_id
  FROM stored_files sf JOIN capability_invocations i ON i.invocation_id=NEW.invocation_id
  WHERE sf.file_id=NEW.markdown_file_id AND sf.account_id=i.account_id
    AND sf.source_run_id=NEW.run_id AND sf.status='ready'
  ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_research_summary AFTER UPDATE OF markdown_file_id ON research_reports
 FOR EACH ROW EXECUTE FUNCTION index_invocation_research_summary();
