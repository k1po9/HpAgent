SET search_path TO hpagent, public;

-- Run remains admitted/running while no execution segment owns resources.
CREATE TABLE agent_execution_segments (
  segment_id text PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  account_id uuid NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  fencing_token bigint,
  state text NOT NULL CHECK (state IN ('requested','active','released')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE account_execution_leases ADD COLUMN owner_segment_id text;

CREATE TABLE agent_run_waits (
  wait_id text PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  account_id uuid NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  operation_id text NOT NULL,
  reason text NOT NULL,
  resume_ref text NOT NULL,
  deadline timestamptz NOT NULL,
  state text NOT NULL CHECK (state IN ('waiting','resumed','cancelled','expired')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_agent_run_waits__run ON agent_run_waits(run_id, state);
GRANT SELECT,INSERT,UPDATE ON agent_execution_segments,agent_run_waits TO hpagent_worker;
