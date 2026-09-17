SET search_path TO hpagent, public;

-- Durable Agent control/data-plane support. Temporal owns compact control
-- state; these tables retain large transcript payloads, idempotency records,
-- and cross-Activity execution ownership.
ALTER TABLE runs
  ADD COLUMN agent_strategy text NOT NULL DEFAULT 'react';
ALTER TABLE runs
  ADD CONSTRAINT ck_runs__agent_strategy
  CHECK (agent_strategy IN ('react', 'plan_and_execute'));

CREATE TABLE agent_transcripts (
  transcript_id text PRIMARY KEY,
  run_id uuid NOT NULL UNIQUE,
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  session_id uuid NOT NULL,
  schema_version smallint NOT NULL DEFAULT 1,
  version bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_agent_transcripts__runs
    FOREIGN KEY (account_id, conversation_id, run_id)
    REFERENCES runs(account_id, conversation_id, run_id) ON DELETE CASCADE,
  CONSTRAINT ck_agent_transcripts__schema_version CHECK (schema_version >= 1),
  CONSTRAINT ck_agent_transcripts__version CHECK (version >= 0)
);

CREATE TABLE agent_transcript_events (
  transcript_id text NOT NULL,
  sequence bigint NOT NULL,
  event_type text NOT NULL,
  operation_id text,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (transcript_id, sequence),
  CONSTRAINT fk_agent_transcript_events__transcript
    FOREIGN KEY (transcript_id) REFERENCES agent_transcripts(transcript_id)
    ON DELETE CASCADE,
  CONSTRAINT uq_agent_transcript_events__operation
    UNIQUE (transcript_id, operation_id),
  CONSTRAINT ck_agent_transcript_events__sequence CHECK (sequence >= 1),
  CONSTRAINT ck_agent_transcript_events__event_type
    CHECK (event_type IN ('context', 'model_decision', 'tool_result',
                          'plan', 'system', 'final_result')),
  CONSTRAINT ck_agent_transcript_events__payload
    CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX ix_agent_transcript_events__load
  ON agent_transcript_events(transcript_id, sequence);

CREATE TABLE agent_operations (
  operation_id text PRIMARY KEY,
  run_id uuid NOT NULL,
  operation_type text NOT NULL,
  status text NOT NULL DEFAULT 'started',
  result_ref text,
  result_payload jsonb,
  error_code text,
  attempt_count integer NOT NULL DEFAULT 1,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_agent_operations__runs
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT ck_agent_operations__type
    CHECK (operation_type IN ('context', 'model', 'tool', 'planning',
                              'synthesis', 'result')),
  CONSTRAINT ck_agent_operations__status
    CHECK (status IN ('started', 'completed', 'failed')),
  CONSTRAINT ck_agent_operations__attempt_count CHECK (attempt_count >= 1),
  CONSTRAINT ck_agent_operations__result_payload
    CHECK (result_payload IS NULL OR jsonb_typeof(result_payload) = 'object'),
  CONSTRAINT ck_agent_operations__completion
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);
CREATE INDEX ix_agent_operations__run
  ON agent_operations(run_id, started_at, operation_id);
CREATE UNIQUE INDEX uq_agent_operations__result_ref
  ON agent_operations(result_ref) WHERE result_ref IS NOT NULL;

CREATE TABLE account_execution_leases (
  account_id uuid PRIMARY KEY,
  owner_run_id uuid,
  fencing_token bigint NOT NULL DEFAULT 0,
  lease_expires_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_account_execution_leases__accounts
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE,
  CONSTRAINT fk_account_execution_leases__runs
    FOREIGN KEY (owner_run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT ck_account_execution_leases__fencing_token
    CHECK (fencing_token >= 0),
  CONSTRAINT ck_account_execution_leases__shape
    CHECK ((owner_run_id IS NULL) = (lease_expires_at IS NULL))
);

GRANT SELECT, INSERT, UPDATE, DELETE ON
  agent_transcripts,
  agent_transcript_events,
  agent_operations,
  account_execution_leases
TO hpagent_worker;
