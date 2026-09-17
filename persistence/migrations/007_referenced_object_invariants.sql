SET search_path TO hpagent, public;

DROP TRIGGER IF EXISTS ct_web_auth_sessions__binding ON web_auth_sessions;
DROP FUNCTION IF EXISTS enforce_web_auth_binding_invariant();

CREATE OR REPLACE FUNCTION validate_web_auth_session(target_session uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE auth web_auth_sessions%ROWTYPE;
BEGIN
  SELECT * INTO auth FROM web_auth_sessions WHERE web_auth_session_id=target_session FOR KEY SHARE;
  IF NOT FOUND THEN RETURN; END IF;
  IF NOT EXISTS (SELECT 1 FROM identity_bindings b
    WHERE b.account_id=auth.account_id AND b.identity_binding_id=auth.identity_binding_id
      AND b.provider='web' AND b.status='active' AND b.verified_at IS NOT NULL) THEN
    RAISE EXCEPTION 'web auth session requires active verified web binding';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION enforce_web_auth_session_invariants()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid;
BEGIN
  IF TG_TABLE_NAME='web_auth_sessions' THEN
    PERFORM validate_web_auth_session(COALESCE(NEW.web_auth_session_id,OLD.web_auth_session_id));
  ELSE
    FOR target IN SELECT web_auth_session_id FROM web_auth_sessions
      WHERE identity_binding_id IN (OLD.identity_binding_id,NEW.identity_binding_id)
      ORDER BY web_auth_session_id
    LOOP PERFORM validate_web_auth_session(target); END LOOP;
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER ct_web_auth_sessions__binding
AFTER INSERT OR UPDATE ON web_auth_sessions DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_web_auth_session_invariants();
CREATE CONSTRAINT TRIGGER ct_identity_bindings__auth_sessions
AFTER UPDATE OR DELETE ON identity_bindings DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_web_auth_session_invariants();

DROP TRIGGER IF EXISTS ct_sessions__predecessor ON sessions;
DROP FUNCTION IF EXISTS enforce_session_predecessor_invariant();

CREATE OR REPLACE FUNCTION validate_session_predecessor(target_session uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE current_session sessions%ROWTYPE;
BEGIN
  SELECT * INTO current_session FROM sessions WHERE session_id=target_session FOR KEY SHARE;
  IF NOT FOUND OR current_session.predecessor_session_id IS NULL THEN RETURN; END IF;
  IF NOT EXISTS (SELECT 1 FROM sessions p
    WHERE p.session_id=current_session.predecessor_session_id
      AND p.account_id=current_session.account_id
      AND p.conversation_id=current_session.conversation_id
      AND p.sequence < current_session.sequence) THEN
    RAISE EXCEPTION 'session predecessor must be earlier';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION enforce_session_predecessor_invariants()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid;
BEGIN
  PERFORM validate_session_predecessor(COALESCE(NEW.session_id,OLD.session_id));
  FOR target IN SELECT session_id FROM sessions
    WHERE predecessor_session_id IN (OLD.session_id,NEW.session_id) ORDER BY session_id
  LOOP PERFORM validate_session_predecessor(target); END LOOP;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ct_sessions__predecessor
AFTER INSERT OR UPDATE OR DELETE ON sessions DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_session_predecessor_invariants();
