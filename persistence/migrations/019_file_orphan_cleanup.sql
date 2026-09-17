-- FILE-P0-02/P0-08: include unbound ready objects in the TTL cleanup scan.
SET search_path TO hpagent, public;

DROP INDEX IF EXISTS ix_stored_files__cleanup;
CREATE INDEX ix_stored_files__cleanup ON stored_files(expires_at,file_id)
  WHERE status IN ('uploading','ready','rejected','deleted');
