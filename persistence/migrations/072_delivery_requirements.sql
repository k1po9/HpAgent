-- Historical requirements remain NULL and retain their original hash and contract.
ALTER TABLE work_requirements ADD COLUMN deliverables jsonb;
ALTER TABLE work_requirements ADD CONSTRAINT ck_work_requirements__deliverables CHECK (
 deliverables IS NULL OR (jsonb_typeof(deliverables)='object'
 AND deliverables->>'schema_version'='1' AND jsonb_typeof(deliverables->'items')='array'
 AND jsonb_array_length(deliverables->'items') BETWEEN 1 AND 10) IS TRUE);

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
    IF q.deliverables IS NOT NULL THEN expected:=expected || jsonb_build_object('deliverables',q.deliverables); END IF;
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

-- Additional checks preserve the existing requirement and completion triggers.
CREATE FUNCTION validate_delivery_requirement() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t jsonb; c jsonb; used text[]:=ARRAY[]::text[]; ids text[]:=ARRAY[]::text[]; cid text; kind text;
BEGIN
 IF NEW.deliverables IS NULL THEN RETURN NEW; END IF;
 IF NOT work_json(NEW.deliverables,ARRAY['schema_version','items'],32768) THEN
   RAISE EXCEPTION 'invalid deliverables'; END IF;
 FOR t IN SELECT * FROM jsonb_array_elements(NEW.deliverables->'items') LOOP
   IF jsonb_typeof(t) IS DISTINCT FROM 'object' OR NOT (t ?& ARRAY['id','format','required','output_criterion_id','format_criterion_id'])
     OR (t-ARRAY['id','format','required','output_criterion_id','format_criterion_id'])<>'{}'::jsonb
     OR jsonb_typeof(t->'id') IS DISTINCT FROM 'string' OR length(t->>'id') NOT BETWEEN 1 AND 80
     OR t->>'id'=ANY(ids) OR t->>'format' NOT IN ('markdown','docx')
     OR jsonb_typeof(t->'format') IS DISTINCT FROM 'string'
     OR jsonb_typeof(t->'required') IS DISTINCT FROM 'boolean' THEN
     RAISE EXCEPTION 'invalid delivery target'; END IF;
   ids:=array_append(ids,t->>'id');
   FOREACH kind IN ARRAY ARRAY['output_exists','check_passed'] LOOP
     cid:=t->>(CASE kind WHEN 'output_exists' THEN 'output_criterion_id' ELSE 'format_criterion_id' END);
     IF cid IS NULL OR cid=ANY(used) THEN RAISE EXCEPTION 'delivery criteria must be distinct'; END IF;
     SELECT value INTO c FROM jsonb_array_elements(NEW.acceptance_criteria) WHERE value->>'id'=cid;
     IF c IS NULL OR c->'required' IS DISTINCT FROM t->'required'
       OR c->'evidence_types' IS DISTINCT FROM jsonb_build_array(kind) THEN
       RAISE EXCEPTION 'delivery criteria mismatch'; END IF;
     used:=array_append(used,cid);
   END LOOP;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_work_requirements__delivery BEFORE INSERT ON work_requirements
 FOR EACH ROW EXECUTE FUNCTION validate_delivery_requirement();

CREATE FUNCTION enforce_delivery_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE q work_requirements%ROWTYPE; t jsonb; subjects text[]; assigned text[]:=ARRAY[]::text[];
BEGIN
 IF NEW.status<>'completed' OR OLD.status='completed' THEN RETURN NEW; END IF;
 SELECT * INTO q FROM work_requirements WHERE account_id=NEW.account_id AND work_id=NEW.work_id
   AND revision=NEW.current_requirement_revision;
 IF q.deliverables IS NULL THEN RETURN NEW; END IF;
 FOR t IN SELECT value FROM jsonb_array_elements(q.deliverables->'items') WHERE value->>'required'='true' LOOP
   SELECT array_agg(DISTINCT oe.predicate->>'subject_ref') INTO subjects
   FROM jsonb_array_elements(NEW.completion_receipt->'evidence') o
   JOIN capability_acceptance_evidence oe ON oe.account_id=NEW.account_id
     AND oe.run_id=(NEW.completion_receipt->>'run_id')::uuid AND oe.evidence_id=(o->>'ref')::uuid
     AND oe.criterion_id=t->>'output_criterion_id' AND o->>'criterion_id'=oe.criterion_id
   JOIN capability_acceptance_evidence fe ON fe.account_id=oe.account_id AND fe.run_id=oe.run_id
     AND fe.criterion_id=t->>'format_criterion_id' AND fe.predicate->>'kind'='check_passed'
     AND fe.predicate->>'subject_ref'=oe.predicate->>'subject_ref'
     AND fe.predicate->>'verifier_key'='file.format.' || (t->>'format')
     AND fe.predicate->>'verifier_version'='1'
   JOIN jsonb_array_elements(NEW.completion_receipt->'evidence') f ON f->>'ref'=fe.evidence_id::text
     AND f->>'criterion_id'=fe.criterion_id AND f->>'type'='check_passed'
   WHERE o->>'type'='output_exists' AND oe.predicate->>'kind'='output_exists';
   IF cardinality(subjects) IS DISTINCT FROM 1 OR subjects[1]=ANY(assigned) THEN
     RAISE EXCEPTION 'delivery existence and format need the same distinct ResultRef'; END IF;
   assigned:=array_append(assigned,subjects[1]);
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_works__delivery_completion BEFORE UPDATE ON works
 FOR EACH ROW EXECUTE FUNCTION enforce_delivery_completion();
