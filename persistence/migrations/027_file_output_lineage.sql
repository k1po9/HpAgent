-- FILE-F3: immutable output lineage and database-owned version numbers.
SET search_path TO hpagent, public;

ALTER TABLE stored_files ADD COLUMN parent_file_id uuid;
ALTER TABLE stored_files ADD COLUMN version integer NOT NULL DEFAULT 1;

ALTER TABLE stored_files ADD CONSTRAINT fk_stored_files__parent
  FOREIGN KEY(account_id,conversation_id,parent_file_id)
  REFERENCES stored_files(account_id,conversation_id,file_id) ON DELETE RESTRICT;
ALTER TABLE stored_files ADD CONSTRAINT ck_stored_files__lineage
  CHECK(parent_file_id IS NULL OR parent_file_id <> file_id);
ALTER TABLE stored_files ADD CONSTRAINT ck_stored_files__version CHECK(version >= 1);
CREATE INDEX ix_stored_files__parent ON stored_files(parent_file_id,created_at,file_id)
  WHERE parent_file_id IS NOT NULL;

CREATE OR REPLACE FUNCTION enforce_stored_file_lineage() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE parent stored_files%ROWTYPE;
BEGIN
  IF NEW.parent_file_id IS NULL THEN
    NEW.version := 1;
    RETURN NEW;
  END IF;
  IF NEW.purpose <> 'output' THEN
    RAISE EXCEPTION 'only output files may have a parent';
  END IF;
  SELECT * INTO parent FROM stored_files
    WHERE account_id=NEW.account_id AND conversation_id=NEW.conversation_id
      AND file_id=NEW.parent_file_id;
  IF NOT FOUND OR parent.status <> 'ready' THEN
    RAISE EXCEPTION 'output parent must be a ready file in the same scope';
  END IF;
  NEW.version := parent.version + 1;
  RETURN NEW;
END $$;

CREATE TRIGGER tr_stored_files__lineage
  BEFORE INSERT OR UPDATE OF parent_file_id,version,account_id,conversation_id,purpose
  ON stored_files FOR EACH ROW EXECUTE FUNCTION enforce_stored_file_lineage();
