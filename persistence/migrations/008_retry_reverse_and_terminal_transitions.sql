SET search_path TO hpagent, public;

-- Revalidate both a changed Run and every retry child that depends on its
-- terminal status, trigger Message, and context watermark.
DROP TRIGGER IF EXISTS ct_runs__message_invariants ON runs;
CREATE OR REPLACE FUNCTION enforce_run_message_invariants()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid;
BEGIN
  IF TG_TABLE_NAME = 'runs' THEN
    IF TG_OP = 'INSERT' THEN
      FOR target IN
        SELECT run_id FROM (
          SELECT NEW.run_id
          UNION SELECT r.run_id FROM runs r WHERE r.retry_of_run_id = NEW.run_id
        ) affected ORDER BY run_id
      LOOP PERFORM validate_run_message_invariant(target); END LOOP;
    ELSIF TG_OP = 'UPDATE' THEN
      FOR target IN
        SELECT run_id FROM (
          SELECT OLD.run_id
          UNION SELECT NEW.run_id
          UNION SELECT r.run_id FROM runs r
            WHERE r.retry_of_run_id IN (OLD.run_id, NEW.run_id)
        ) affected ORDER BY run_id
      LOOP PERFORM validate_run_message_invariant(target); END LOOP;
    ELSE
      FOR target IN
        SELECT r.run_id FROM runs r WHERE r.retry_of_run_id = OLD.run_id ORDER BY r.run_id
      LOOP PERFORM validate_run_message_invariant(target); END LOOP;
    END IF;
  ELSE
    IF TG_OP = 'INSERT' THEN
      FOR target IN
        SELECT run_id FROM (
          SELECT NEW.produced_by_run_id AS run_id
          UNION SELECT r.run_id FROM runs r WHERE r.trigger_message_id = NEW.message_id
        ) affected WHERE run_id IS NOT NULL ORDER BY run_id
      LOOP PERFORM validate_run_message_invariant(target); END LOOP;
    ELSIF TG_OP = 'UPDATE' THEN
      FOR target IN
        SELECT run_id FROM (
          SELECT OLD.produced_by_run_id AS run_id
          UNION SELECT NEW.produced_by_run_id
          UNION SELECT r.run_id FROM runs r
            WHERE r.trigger_message_id IN (OLD.message_id, NEW.message_id)
        ) affected WHERE run_id IS NOT NULL ORDER BY run_id
      LOOP PERFORM validate_run_message_invariant(target); END LOOP;
    ELSE
      FOR target IN
        SELECT run_id FROM (
          SELECT OLD.produced_by_run_id AS run_id
          UNION SELECT r.run_id FROM runs r WHERE r.trigger_message_id = OLD.message_id
        ) affected WHERE run_id IS NOT NULL ORDER BY run_id
      LOOP PERFORM validate_run_message_invariant(target); END LOOP;
    END IF;
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER ct_runs__message_invariants
AFTER INSERT OR UPDATE OR DELETE ON runs DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_run_message_invariants();

CREATE OR REPLACE FUNCTION enforce_run_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = OLD.status THEN RETURN NEW; END IF;
  IF (OLD.status = 'queued' AND NEW.status IN ('running','cancelling','failed','cancelled'))
    OR (OLD.status = 'running' AND NEW.status IN ('cancelling','completed','failed','cancelled'))
    OR (OLD.status = 'cancelling' AND NEW.status IN ('completed','failed','cancelled')) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'invalid run status transition';
END $$;

CREATE TRIGGER tr_runs__status_transition
BEFORE UPDATE OF status ON runs
FOR EACH ROW EXECUTE FUNCTION enforce_run_status_transition();

CREATE OR REPLACE FUNCTION enforce_assistant_message_status_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.role <> 'assistant' OR NEW.status = OLD.status THEN RETURN NEW; END IF;
  IF OLD.status = 'pending' AND NEW.status IN ('completed','failed','aborted') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'invalid assistant message status transition';
END $$;

CREATE TRIGGER tr_messages__assistant_status_transition
BEFORE UPDATE OF status ON messages
FOR EACH ROW EXECUTE FUNCTION enforce_assistant_message_status_transition();
