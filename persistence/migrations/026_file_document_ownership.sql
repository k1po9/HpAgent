-- Bind normalized document ownership to the exact authoritative Run file tuple.
SET search_path TO hpagent, public;

ALTER TABLE run_files ADD CONSTRAINT uq_run_files__account_run_file
  UNIQUE(account_id,run_id,file_id);
ALTER TABLE normalized_documents ADD CONSTRAINT fk_normalized_documents__owned_run_file
  FOREIGN KEY(account_id,run_id,file_id)
  REFERENCES run_files(account_id,run_id,file_id) ON DELETE CASCADE;
