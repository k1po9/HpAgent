SET search_path TO hpagent, public;

-- ArtifactService.create_version serializes version allocation by locking the
-- owned Artifact row with SELECT ... FOR UPDATE. PostgreSQL requires UPDATE
-- privilege for that row lock. The API still cannot UPDATE artifact_versions;
-- queued -> running -> terminal remains Worker-only.
GRANT UPDATE ON artifacts TO hpagent_api;
