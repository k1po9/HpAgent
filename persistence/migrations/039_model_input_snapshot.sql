SET search_path TO hpagent, public;

CREATE TABLE model_input_snapshots (
  snapshot_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  run_id uuid NOT NULL,
  model_call_id uuid NOT NULL,
  operation_id varchar(200) NOT NULL,
  execution_attempt integer NOT NULL,
  call_ordinal integer NOT NULL,
  fallback_attempt integer NOT NULL,
  phase text NOT NULL,
  endpoint_id text NOT NULL,
  provider text NOT NULL,
  model text NOT NULL,
  api_format text NOT NULL,
  provider_request_body jsonb NOT NULL,
  content_hash bytea NOT NULL,
  serializer_version text NOT NULL,
  entitlement_version bigint NOT NULL,
  supersedes_snapshot_id uuid REFERENCES model_input_snapshots(snapshot_id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_model_input_snapshots__run_owner
    FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT,
  CONSTRAINT uq_model_input_snapshots__operation UNIQUE(account_id,run_id,operation_id),
  CONSTRAINT uq_model_input_snapshots__call_attempt
    UNIQUE(model_call_id,fallback_attempt),
  CONSTRAINT ck_model_input_snapshots__positive
    CHECK(execution_attempt > 0 AND call_ordinal > 0 AND fallback_attempt > 0
      AND entitlement_version > 0),
  CONSTRAINT ck_model_input_snapshots__strings CHECK(
    length(phase) > 0 AND length(endpoint_id) > 0 AND length(provider) > 0
    AND length(model) > 0 AND length(api_format) > 0
    AND length(serializer_version) > 0
  ),
  CONSTRAINT ck_model_input_snapshots__body_object
    CHECK(jsonb_typeof(provider_request_body) = 'object')
);

CREATE INDEX ix_model_input_snapshots__account_run_created
  ON model_input_snapshots(account_id,run_id,created_at);
CREATE INDEX ix_model_input_snapshots__model_call
  ON model_input_snapshots(model_call_id,fallback_attempt);

ALTER TABLE account_model_usage_ledger ADD CONSTRAINT
  fk_account_model_usage_ledger__snapshot FOREIGN KEY(snapshot_id)
  REFERENCES model_input_snapshots(snapshot_id) ON DELETE RESTRICT;

GRANT SELECT,INSERT ON model_input_snapshots TO hpagent_worker;
GRANT SELECT ON model_input_snapshots TO hpagent_api;
