-- Invocation limits project the existing usage ledger; never charge parent and child twice.
SET search_path TO hpagent, public;
CREATE FUNCTION enforce_capability_usage_limit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE i capability_invocations%ROWTYPE; cap bigint; consumed bigint;
BEGIN
 IF NEW.execution_id IS NULL OR NEW.dimension NOT IN ('model_calls','tool_calls') THEN RETURN NEW; END IF;
 SELECT c.* INTO i FROM capability_invocations c WHERE c.child_execution_id=NEW.execution_id;
 IF NOT FOUND THEN RETURN NEW; END IF;
 IF NEW.run_id IS DISTINCT FROM i.run_id THEN RAISE EXCEPTION 'capability usage Run mismatch'; END IF;
 cap := (i.descriptor->'limits'->>CASE NEW.dimension
   WHEN 'model_calls' THEN 'max_model_calls' ELSE 'max_tool_calls' END)::bigint;
 -- RunBudgetService holds the Run budget row lock before inserting usage, so concurrent
 -- operations share the same serialization point as Account/day and Work reservations.
 SELECT COALESCE(sum(CASE WHEN state='settled' THEN actual_amount ELSE reserved_amount END),0)
 INTO consumed FROM run_usage_ledger WHERE execution_id=NEW.execution_id
   AND dimension=NEW.dimension AND state IN ('reserved','settled');
 IF consumed+NEW.reserved_amount>cap THEN RAISE EXCEPTION 'capability invocation call limit exceeded'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tr_capability_usage_limit BEFORE INSERT ON run_usage_ledger
 FOR EACH ROW EXECUTE FUNCTION enforce_capability_usage_limit();
CREATE INDEX ix_capability_active_executions ON run_executions(account_id,parent_execution_id)
 WHERE role='capability' AND status IN ('queued','running','cancelling');
REVOKE ALL ON FUNCTION enforce_capability_usage_limit() FROM PUBLIC;
