-- FILE-F3: a ready file's lineage identity is immutable.
SET search_path TO hpagent, public;

CREATE OR REPLACE FUNCTION enforce_stored_file_lineage() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE parent stored_files%ROWTYPE;
BEGIN
  IF TG_OP='UPDATE' AND OLD.status='ready' AND (
    NEW.parent_file_id IS DISTINCT FROM OLD.parent_file_id OR
    NEW.version IS DISTINCT FROM OLD.version OR
    NEW.account_id IS DISTINCT FROM OLD.account_id OR
    NEW.conversation_id IS DISTINCT FROM OLD.conversation_id OR
    NEW.purpose IS DISTINCT FROM OLD.purpose
  ) THEN
    RAISE EXCEPTION 'ready file lineage is immutable';
  END IF;
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
