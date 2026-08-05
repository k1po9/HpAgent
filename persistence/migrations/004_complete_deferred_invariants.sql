SET search_path TO hpagent, public;

DROP TRIGGER IF EXISTS ct_runs__message_invariants ON runs;
DROP TRIGGER IF EXISTS ct_messages__run_invariants ON messages;
DROP FUNCTION IF EXISTS enforce_run_message_invariants();

CREATE OR REPLACE FUNCTION validate_run_message_invariant(target_run uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE; trigger_msg messages%ROWTYPE; agent_msg messages%ROWTYPE;
BEGIN
  SELECT * INTO r FROM runs WHERE run_id = target_run FOR KEY SHARE;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT * INTO trigger_msg FROM messages WHERE message_id = r.trigger_message_id FOR KEY SHARE;
  IF NOT FOUND OR trigger_msg.role <> 'user' OR trigger_msg.status <> 'accepted' THEN
    RAISE EXCEPTION 'run trigger must be an accepted user message';
  END IF;
  IF r.retry_of_run_id IS NULL AND r.context_message_seq <> trigger_msg.sequence THEN
    RAISE EXCEPTION 'initial run context watermark must equal trigger sequence';
  END IF;
  IF r.retry_of_run_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM runs p WHERE p.run_id = r.retry_of_run_id
      AND p.status IN ('failed','cancelled')
      AND p.trigger_message_id = r.trigger_message_id
      AND p.context_message_seq = r.context_message_seq
  ) THEN RAISE EXCEPTION 'invalid retry source'; END IF;
  SELECT * INTO agent_msg FROM messages WHERE produced_by_run_id = r.run_id FOR KEY SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'each run must have exactly one assistant message'; END IF;
  IF (r.status IN ('queued','running','cancelling') AND agent_msg.status <> 'pending')
    OR (r.status = 'completed' AND agent_msg.status <> 'completed')
    OR (r.status = 'failed' AND agent_msg.status <> 'failed')
    OR (r.status = 'cancelled' AND agent_msg.status <> 'aborted') THEN
    RAISE EXCEPTION 'run and assistant states disagree';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION enforce_run_message_invariants()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid;
BEGIN
  IF TG_TABLE_NAME = 'runs' THEN
    PERFORM validate_run_message_invariant(COALESCE(NEW.run_id, OLD.run_id));
  ELSE
    FOR target IN
      SELECT DISTINCT run_id FROM (
        SELECT OLD.produced_by_run_id AS run_id
        UNION ALL SELECT NEW.produced_by_run_id
        UNION ALL SELECT r.run_id FROM runs r
          WHERE r.trigger_message_id IN (OLD.message_id, NEW.message_id)
      ) affected WHERE run_id IS NOT NULL ORDER BY run_id
    LOOP PERFORM validate_run_message_invariant(target); END LOOP;
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER ct_runs__message_invariants
AFTER INSERT OR UPDATE ON runs DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_run_message_invariants();
CREATE CONSTRAINT TRIGGER ct_messages__run_invariants
AFTER INSERT OR UPDATE OR DELETE ON messages DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_run_message_invariants();

CREATE OR REPLACE FUNCTION enforce_web_auth_binding_invariant()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM identity_bindings b
    WHERE b.account_id=NEW.account_id AND b.identity_binding_id=NEW.identity_binding_id
      AND b.provider='web' AND b.status='active' AND b.verified_at IS NOT NULL) THEN
    RAISE EXCEPTION 'web auth session requires active verified web binding';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ct_web_auth_sessions__binding
AFTER INSERT OR UPDATE ON web_auth_sessions DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_web_auth_binding_invariant();

CREATE OR REPLACE FUNCTION enforce_session_predecessor_invariant()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.predecessor_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sessions p WHERE p.session_id=NEW.predecessor_session_id
      AND p.account_id=NEW.account_id AND p.conversation_id=NEW.conversation_id
      AND p.sequence < NEW.sequence
  ) THEN RAISE EXCEPTION 'session predecessor must be earlier'; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ct_sessions__predecessor
AFTER INSERT OR UPDATE ON sessions DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_session_predecessor_invariant();
