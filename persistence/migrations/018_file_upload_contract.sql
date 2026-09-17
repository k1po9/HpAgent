-- FILE-P0-03: retain optional client digest until streaming validation completes.
SET search_path TO hpagent, public;

ALTER TABLE stored_files ADD COLUMN declared_sha256 char(64);
ALTER TABLE stored_files ADD CONSTRAINT ck_stored_files__declared_sha256
  CHECK(declared_sha256 IS NULL OR declared_sha256 ~ '^[0-9a-f]{64}$');

CREATE OR REPLACE FUNCTION enforce_api_input_file_only() RETURNS trigger
LANGUAGE plpgsql SET search_path=hpagent,pg_temp AS $$
BEGIN
  IF current_user='hpagent_api' AND NEW.purpose<>'input' THEN
    RAISE EXCEPTION 'API role may only create or update input files';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_stored_files__api_input_only
  BEFORE INSERT OR UPDATE ON stored_files
  FOR EACH ROW EXECUTE FUNCTION enforce_api_input_file_only();

REVOKE UPDATE ON run_budgets FROM hpagent_api;
