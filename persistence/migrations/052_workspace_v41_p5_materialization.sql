SET search_path TO hpagent, public;
ALTER TABLE run_resource_candidates ADD COLUMN materialized_at timestamptz;
