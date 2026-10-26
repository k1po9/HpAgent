-- Governance reuses the two source/action tables. No business data import.
ALTER TABLE memory_actions ADD COLUMN send_state text NOT NULL DEFAULT 'idle'
 CHECK(send_state IN ('idle','sending','settled','unknown'));
ALTER TABLE memory_actions ADD COLUMN send_uncertain boolean NOT NULL DEFAULT false;
-- Pre-upgrade requests have no sender-quiescence evidence.
UPDATE memory_actions SET send_state='unknown',send_uncertain=true WHERE kind='retain' AND status<>'queued';
ALTER TABLE run_executions ADD COLUMN memory_context jsonb;
ALTER TABLE model_input_snapshots ADD COLUMN memory_context jsonb;
CREATE INDEX ix_memory_invalidations ON memory_actions(account_id,invalidation_epoch)
 WHERE invalidation_epoch IS NOT NULL;
GRANT INSERT,UPDATE ON memory_sources TO hpagent_api;
GRANT INSERT ON memory_actions TO hpagent_api;
GRANT SELECT(send_state,send_uncertain) ON memory_actions TO hpagent_api;
-- Command receipts never contain correction plaintext. Frozen requests are worker-only.
GRANT UPDATE(memory_epoch) ON accounts TO hpagent_api,hpagent_worker;
-- Existing executions remain unknown; new executions start without memory.
ALTER TABLE run_executions ALTER COLUMN memory_context SET DEFAULT
 '{"epoch":0,"scopes":[],"documents":[]}'::jsonb;
CREATE FUNCTION inherit_memory_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.parent_execution_id IS NOT NULL THEN
   SELECT memory_context INTO NEW.memory_context FROM run_executions
    WHERE account_id=NEW.account_id AND execution_id=NEW.parent_execution_id;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_inherit_memory_context BEFORE INSERT ON run_executions
 FOR EACH ROW EXECUTE FUNCTION inherit_memory_context();
GRANT UPDATE(invalidation_epoch,affected_scopes) ON memory_actions TO hpagent_api;
