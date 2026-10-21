-- Generic required saves share the exact parent save facts and destination policy.
SET search_path TO hpagent, public;
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
  ) AND NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(receipt->'evidence') e
    JOIN capability_acceptance_evidence ce ON ce.account_id=NEW.account_id AND ce.run_id=r.run_id
      AND ce.evidence_id=(e->>'ref')::uuid AND e->>'type'='workspace_saved'
    JOIN workspace_nodes n ON n.account_id=ce.account_id AND n.node_id=(ce.predicate->>'target_id')::uuid
    WHERE n.deleted_at IS NULL AND capability_predicate_valid(NEW.account_id,r.run_id,ce.predicate)
      AND CASE WHEN q.deliverable_policy->>'entry_id' IS NOT NULL
      THEN n.node_id=(q.deliverable_policy->>'entry_id')::uuid
      ELSE n.parent_id=(q.deliverable_policy->>'directory_id')::uuid END
  ) THEN RAISE EXCEPTION 'required save receipt missing'; END IF;
  RETURN NEW;
END $$;


