-- Run owns durable execution data. Chat context is optional for other sources.
SET search_path TO hpagent, public;

CREATE UNIQUE INDEX uq_runs__account_run ON runs(account_id,run_id);
ALTER TABLE agent_transcripts ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE agent_transcripts ALTER COLUMN session_id DROP NOT NULL;
-- The existing composite Chat FK remains; this FK also protects NULL contexts.
ALTER TABLE agent_transcripts ADD CONSTRAINT fk_agent_transcripts__run_owner
  FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE CASCADE;
