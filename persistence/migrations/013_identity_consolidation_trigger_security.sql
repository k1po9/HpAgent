SET search_path TO hpagent, public;

-- Identity consolidation is executed by the narrowly privileged Worker role.
-- The deferred invariant trigger must inspect complete Web session rows, but
-- granting the Worker direct SELECT on token/csrf hashes would break the API /
-- Worker credential boundary. Execute the trigger as its migration-role owner
-- with an immutable safe search_path instead.
ALTER FUNCTION enforce_web_auth_session_invariants() SECURITY DEFINER;
ALTER FUNCTION enforce_web_auth_session_invariants()
  SET search_path TO hpagent, pg_temp;
REVOKE ALL ON FUNCTION enforce_web_auth_session_invariants() FROM PUBLIC;
