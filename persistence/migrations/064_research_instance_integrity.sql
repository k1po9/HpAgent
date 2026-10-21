-- Source/evidence links and historical instance identities must survive later updates too.
SET search_path TO hpagent, public;
ALTER TABLE source_records ADD UNIQUE(run_id,invocation_id,source_id);
ALTER TABLE evidence_items ADD UNIQUE(run_id,invocation_id,evidence_id);
ALTER TABLE research_claims ADD UNIQUE(run_id,invocation_id,claim_id);
ALTER TABLE evidence_items ADD FOREIGN KEY(run_id,invocation_id,source_id)
 REFERENCES source_records(run_id,invocation_id,source_id);
ALTER TABLE research_citations ADD FOREIGN KEY(run_id,invocation_id,evidence_id)
 REFERENCES evidence_items(run_id,invocation_id,evidence_id);
ALTER TABLE research_citations ADD FOREIGN KEY(run_id,invocation_id,claim_id)
 REFERENCES research_claims(run_id,invocation_id,claim_id);
CREATE FUNCTION immutable_research_identity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF ROW(NEW.run_id,NEW.invocation_id) IS DISTINCT FROM ROW(OLD.run_id,OLD.invocation_id) THEN
  RAISE EXCEPTION 'immutable Research instance identity'; END IF;
 IF TG_TABLE_NAME='research_history_baselines' THEN
  IF ROW(NEW.account_id,NEW.budget_bytes,NEW.selected) IS DISTINCT FROM
     ROW(OLD.account_id,OLD.budget_bytes,OLD.selected) THEN
   RAISE EXCEPTION 'immutable Research history baseline'; END IF;
 END IF;
 IF TG_TABLE_NAME='research_reports' THEN
  IF OLD.markdown_file_id IS NOT NULL AND ROW(NEW.markdown_file_id,NEW.report_markdown,
       NEW.report_structured_json,NEW.snapshot,NEW.daily_diff,NEW.previous_run_id,NEW.previous_invocation_id)
     IS DISTINCT FROM ROW(OLD.markdown_file_id,OLD.report_markdown,OLD.report_structured_json,
       OLD.snapshot,OLD.daily_diff,OLD.previous_run_id,OLD.previous_invocation_id) THEN
   RAISE EXCEPTION 'immutable published Research report'; END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE table_name text; BEGIN
 FOREACH table_name IN ARRAY ARRAY['research_plans','source_records','evidence_items',
   'research_stage_results','research_iterations','research_claims','research_citations',
   'research_reports','research_history_baselines'] LOOP
  EXECUTE format('CREATE TRIGGER tr_research_identity BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_research_identity()',table_name);
 END LOOP;
END $$;
