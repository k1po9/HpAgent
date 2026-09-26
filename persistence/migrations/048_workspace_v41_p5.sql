-- P5 bounded discovery and owner retention diagnostics.
SET search_path TO hpagent, public;
CREATE INDEX ix_workspace_nodes__owner_search ON workspace_nodes(account_id,node_id)
  WHERE kind='file' AND deleted_at IS NULL;
CREATE INDEX ix_workspace_nodes__owner_date ON workspace_nodes(account_id,created_at,node_id)
  WHERE kind='file' AND deleted_at IS NULL;
CREATE INDEX ix_stored_files__source_run ON stored_files(account_id,source_run_id)
  WHERE source_run_id IS NOT NULL;
GRANT SELECT ON output_publish_operations TO hpagent_api;
