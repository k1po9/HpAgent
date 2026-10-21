SET search_path TO hpagent, public;
ALTER TABLE work_events DROP CONSTRAINT work_events_event_type_check;
ALTER TABLE work_events ADD CHECK(event_type IN
 ('accepted','revised','paused','pause_requested','resumed','stopped','stop_requested','linked','advanced',
  'run_succeeded','run_failed','run_cancelled','completed','result_accepted','budget','input','revoke_input',
  'target','disable_target','accept_result','resolve_delivery','grant_resource','revoke_resource',
  'delivery_accepted','delivery_blocked','artifact_reference','blocked','result_changed'));
CREATE FUNCTION validate_work_progress_fact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM capability_invocations i JOIN runs r USING(account_id,run_id)
  WHERE i.account_id=NEW.account_id AND i.invocation_id=NEW.invocation_id
   AND r.work_id=NEW.work_id AND r.requirement_revision=NEW.requirement_revision) THEN
  RAISE EXCEPTION 'progress fact mandate mismatch'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_progress_sample_owner BEFORE INSERT ON work_progress_samples
 FOR EACH ROW EXECUTE FUNCTION validate_work_progress_fact();
CREATE TRIGGER tr_progress_fact_owner BEFORE INSERT ON work_progress_facts
 FOR EACH ROW EXECUTE FUNCTION validate_work_progress_fact();
CREATE FUNCTION validate_daily_result() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM runs r JOIN works w USING(account_id,work_id)
  WHERE r.account_id=NEW.account_id AND r.run_id=NEW.run_id AND r.work_id=NEW.work_id
   AND r.requirement_revision=NEW.requirement_revision AND r.status='succeeded'
   AND w.status='active' AND w.current_requirement_revision=r.requirement_revision
   AND w.control_epoch=r.work_control_epoch) THEN
  RAISE EXCEPTION 'daily result requires current successful mandate'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_daily_result_owner BEFORE INSERT ON work_daily_results
 FOR EACH ROW EXECUTE FUNCTION validate_daily_result();
CREATE FUNCTION validate_daily_result_ref() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM capability_result_refs f JOIN capability_results r USING(account_id,invocation_id)
  WHERE f.account_id=NEW.account_id AND f.ref_id=NEW.ref_id AND r.run_id=NEW.run_id) THEN
  RAISE EXCEPTION 'daily reference producer mismatch'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_daily_ref_owner BEFORE INSERT ON work_daily_result_refs
 FOR EACH ROW EXECUTE FUNCTION validate_daily_result_ref();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['work_progress_samples','work_progress_facts','work_daily_results','work_daily_result_refs'] LOOP
  EXECUTE format('CREATE TRIGGER tr_progress_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_work_fact()',t);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION validate_work_progress_fact(),validate_daily_result(),validate_daily_result_ref() FROM PUBLIC;
