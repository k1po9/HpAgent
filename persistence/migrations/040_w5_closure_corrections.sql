SET search_path TO hpagent, public;

-- Historical rows predate URL binding and cannot be truthfully backfilled.
ALTER TABLE model_input_snapshots ADD COLUMN resolved_url text;
ALTER TABLE model_input_snapshots DROP COLUMN supersedes_snapshot_id;
ALTER TABLE model_input_snapshots ADD CONSTRAINT ck_model_input_snapshots__resolved_url
  CHECK(resolved_url IS NULL OR (
    resolved_url ~ '^https?://[^/?#]+/' AND resolved_url !~ '[?#]'
    AND resolved_url !~ '^https?://[^/]*@'
  ));
