SET search_path TO hpagent, public;

-- API admission inserts queued versions. Generation, failure and publication
-- belong to Worker operations; API needs no UPDATE/row lock on this table.
-- Keep 057 intact for existing databases and retain Work/Research Worker INSERT.
REVOKE UPDATE ON artifact_versions FROM hpagent_api;
