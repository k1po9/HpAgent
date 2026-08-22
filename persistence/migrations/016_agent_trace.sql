SET search_path TO hpagent, public;

-- Agent observability facts.  Temporal remains the control-plane source of
-- truth; these rows are an independently writable/readable debug projection.
CREATE TABLE trace_runs (
  trace_run_id uuid PRIMARY KEY,
  run_id uuid NOT NULL UNIQUE,
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  strategy text NOT NULL,
  status text NOT NULL DEFAULT 'running',
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT fk_trace_runs__runs
    FOREIGN KEY (account_id, conversation_id, run_id)
    REFERENCES runs(account_id, conversation_id, run_id) ON DELETE CASCADE,
  CONSTRAINT ck_trace_runs__status
    CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
  CONSTRAINT ck_trace_runs__timestamps
    CHECK ((status = 'running') = (ended_at IS NULL) AND
           (ended_at IS NULL OR ended_at >= started_at)),
  CONSTRAINT ck_trace_runs__metadata
    CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE INDEX ix_trace_runs__conversation
  ON trace_runs(account_id, conversation_id, started_at DESC, trace_run_id DESC);

CREATE TABLE trace_events (
  trace_event_id uuid PRIMARY KEY,
  trace_run_id uuid NOT NULL,
  parent_event_id uuid,
  event_type text NOT NULL,
  name varchar(200) NOT NULL,
  status text NOT NULL DEFAULT 'running',
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  duration_ms bigint,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT uq_trace_events__run_event UNIQUE(trace_run_id, trace_event_id),
  CONSTRAINT fk_trace_events__trace_run
    FOREIGN KEY (trace_run_id) REFERENCES trace_runs(trace_run_id) ON DELETE CASCADE,
  CONSTRAINT fk_trace_events__parent
    FOREIGN KEY (trace_run_id, parent_event_id)
    REFERENCES trace_events(trace_run_id, trace_event_id) ON DELETE CASCADE,
  CONSTRAINT ck_trace_events__event_type CHECK (length(btrim(event_type)) > 0),
  CONSTRAINT ck_trace_events__name CHECK (length(btrim(name)) > 0),
  CONSTRAINT ck_trace_events__status
    CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
  CONSTRAINT ck_trace_events__timestamps
    CHECK ((status = 'running') = (ended_at IS NULL) AND
           (ended_at IS NULL OR ended_at >= started_at)),
  CONSTRAINT ck_trace_events__duration
    CHECK ((ended_at IS NULL) = (duration_ms IS NULL) AND
           (duration_ms IS NULL OR duration_ms >= 0)),
  CONSTRAINT ck_trace_events__metadata
    CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE INDEX ix_trace_events__tree
  ON trace_events(trace_run_id, started_at, trace_event_id);
CREATE INDEX ix_trace_events__parent
  ON trace_events(trace_run_id, parent_event_id, started_at, trace_event_id);
CREATE UNIQUE INDEX uq_trace_events__one_root
  ON trace_events(trace_run_id) WHERE parent_event_id IS NULL;

-- The Worker records traces.  The API only needs ownership-scoped reads for a
-- later historical Trace Console endpoint.
GRANT SELECT, INSERT, UPDATE ON trace_runs, trace_events TO hpagent_worker;
GRANT SELECT ON trace_runs, trace_events TO hpagent_api;
