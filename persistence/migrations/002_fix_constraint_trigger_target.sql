SET search_path TO hpagent, public;
-- 001 used one generic function for two different row shapes.  Choose the
-- target column by table name so deferred Run and Message triggers both work.
CREATE OR REPLACE FUNCTION enforce_run_message_invariants() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_run uuid; r runs%ROWTYPE; msg messages%ROWTYPE; assistant_count integer;
BEGIN
  IF TG_TABLE_NAME = 'runs' THEN
    target_run := COALESCE(NEW.run_id, OLD.run_id);
  ELSE
    target_run := COALESCE(NEW.produced_by_run_id, OLD.produced_by_run_id);
  END IF;
  IF target_run IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO r FROM runs WHERE run_id=target_run;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO msg FROM messages WHERE message_id=r.trigger_message_id;
  IF NOT FOUND OR msg.role<>'user' OR msg.status<>'accepted' THEN RAISE EXCEPTION 'run trigger must be an accepted user message'; END IF;
  IF r.retry_of_run_id IS NULL AND r.context_message_seq<>msg.sequence THEN RAISE EXCEPTION 'initial run context watermark must equal trigger sequence'; END IF;
  IF r.retry_of_run_id IS NOT NULL THEN
    PERFORM 1 FROM runs p WHERE p.run_id=r.retry_of_run_id AND p.status IN ('failed','cancelled') AND p.trigger_message_id=r.trigger_message_id AND p.context_message_seq=r.context_message_seq;
    IF NOT FOUND THEN RAISE EXCEPTION 'invalid retry source'; END IF;
  END IF;
  SELECT count(*) INTO assistant_count FROM messages WHERE produced_by_run_id=r.run_id AND role='assistant';
  IF assistant_count<>1 THEN RAISE EXCEPTION 'each run must have exactly one assistant message'; END IF;
  SELECT * INTO msg FROM messages WHERE produced_by_run_id=r.run_id;
  IF (r.status='completed' AND msg.status<>'completed') OR (r.status='failed' AND msg.status<>'failed') OR (r.status='cancelled' AND msg.status<>'aborted') THEN RAISE EXCEPTION 'run and assistant terminal states disagree'; END IF;
  RETURN NULL;
END $$;
