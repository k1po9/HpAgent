-- A cancelled queued candidate has a terminal fact without inventing a producer/start.
SET search_path TO hpagent, public;
ALTER TABLE artifact_versions DROP CONSTRAINT ck_artifact_versions__state;
ALTER TABLE artifact_versions ADD CONSTRAINT ck_artifact_versions__state CHECK(
 (status='queued' AND html IS NULL AND failure_code IS NULL AND failure_message IS NULL
   AND started_at IS NULL AND completed_at IS NULL) OR
 (status='running' AND html IS NULL AND failure_code IS NULL AND failure_message IS NULL
   AND started_at IS NOT NULL AND completed_at IS NULL) OR
 (status='completed' AND html IS NOT NULL AND failure_code IS NULL AND failure_message IS NULL
   AND started_at IS NOT NULL AND completed_at IS NOT NULL) OR
 (status='failed' AND html IS NULL AND failure_code IS NOT NULL AND completed_at IS NOT NULL
   AND (started_at IS NOT NULL OR producing_run_id IS NULL))
);
