SET search_path TO hpagent, public;

-- 014 may already be deployed. Extend, rather than rewrite, its operation
-- state machine so an Activity redelivery can distinguish a safe retry from
-- an external side effect whose acknowledgement was lost.
ALTER TABLE agent_operations
  DROP CONSTRAINT ck_agent_operations__status;

ALTER TABLE agent_operations
  ADD CONSTRAINT ck_agent_operations__status
  CHECK (status IN ('started', 'intent_recorded', 'completed', 'failed', 'uncertain'));

ALTER TABLE agent_operations
  DROP CONSTRAINT ck_agent_operations__completion;

ALTER TABLE agent_operations
  ADD CONSTRAINT ck_agent_operations__completion
  CHECK ((status = 'completed') = (completed_at IS NOT NULL));

CREATE INDEX ix_agent_operations__recovery
  ON agent_operations(run_id, status, updated_at)
  WHERE status IN ('intent_recorded', 'uncertain');
