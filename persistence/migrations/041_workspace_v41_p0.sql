-- Workspace v4.1 P0: ownership is the account; conversation is provenance.
SET search_path TO hpagent, public;

ALTER TABLE stored_files ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE stored_files ADD COLUMN source_run_id uuid;
ALTER TABLE stored_files ADD CONSTRAINT fk_stored_files__source_run
  FOREIGN KEY(account_id,source_run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT;

ALTER TABLE message_files DROP CONSTRAINT fk_message_files__file;
ALTER TABLE message_files ADD CONSTRAINT fk_message_files__file
  FOREIGN KEY(account_id,file_id) REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT;
ALTER TABLE run_files DROP CONSTRAINT fk_run_files__run;
ALTER TABLE run_files DROP CONSTRAINT fk_run_files__file;
ALTER TABLE run_files ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE run_files ADD CONSTRAINT fk_run_files__run
  FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT;
ALTER TABLE run_files ADD CONSTRAINT fk_run_files__file
  FOREIGN KEY(account_id,file_id) REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT;
ALTER TABLE stored_files DROP CONSTRAINT fk_stored_files__parent;
ALTER TABLE stored_files ADD CONSTRAINT fk_stored_files__parent
  FOREIGN KEY(account_id,parent_file_id) REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION enforce_file_binding_shape() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE m messages%ROWTYPE; f stored_files%ROWTYPE;
BEGIN
  SELECT * INTO m FROM messages WHERE account_id=NEW.account_id AND message_id=NEW.message_id;
  SELECT * INTO f FROM stored_files WHERE account_id=NEW.account_id AND file_id=NEW.file_id;
  IF f.status <> 'ready' THEN RAISE EXCEPTION 'only ready files may be bound'; END IF;
  IF NEW.role='input' AND m.role<>'user' THEN
    RAISE EXCEPTION 'input files require a user message';
  END IF;
  IF NEW.role='output' AND (m.role<>'assistant' OR m.status<>'completed' OR f.purpose<>'output') THEN
    RAISE EXCEPTION 'output files require a completed assistant message';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION enforce_stored_file_lineage() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE parent stored_files%ROWTYPE;
BEGIN
  IF TG_OP='UPDATE' AND OLD.status='ready' AND (
    NEW.parent_file_id IS DISTINCT FROM OLD.parent_file_id OR
    NEW.version IS DISTINCT FROM OLD.version OR
    NEW.account_id IS DISTINCT FROM OLD.account_id OR
    NEW.conversation_id IS DISTINCT FROM OLD.conversation_id OR
    NEW.source_run_id IS DISTINCT FROM OLD.source_run_id OR
    NEW.purpose IS DISTINCT FROM OLD.purpose
  ) THEN RAISE EXCEPTION 'ready file lineage is immutable'; END IF;
  IF NEW.parent_file_id IS NULL THEN NEW.version := 1; RETURN NEW; END IF;
  IF NEW.purpose <> 'output' THEN RAISE EXCEPTION 'only output files may have a parent'; END IF;
  SELECT * INTO parent FROM stored_files
    WHERE account_id=NEW.account_id AND file_id=NEW.parent_file_id;
  IF NOT FOUND OR parent.status <> 'ready' THEN
    RAISE EXCEPTION 'output parent must be a ready file in the same account';
  END IF;
  NEW.version := parent.version + 1;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION enforce_run_file_account() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE r runs%ROWTYPE; f stored_files%ROWTYPE;
BEGIN
  SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
  IF NOT FOUND OR NEW.conversation_id IS DISTINCT FROM r.conversation_id THEN
    RAISE EXCEPTION 'Run file context does not match Run';
  END IF;
  SELECT * INTO f FROM stored_files
    WHERE account_id=NEW.account_id AND file_id=NEW.file_id;
  IF NOT FOUND OR f.status <> 'ready' THEN
    RAISE EXCEPTION 'Run file must be ready in the same account';
  END IF;
  IF NEW.direction='output' AND (f.purpose<>'output' OR f.source_run_id IS DISTINCT FROM NEW.run_id) THEN
    RAISE EXCEPTION 'published output must originate from this Run';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_run_files__run_context BEFORE INSERT OR UPDATE ON run_files
  FOR EACH ROW EXECUTE FUNCTION enforce_run_file_account();

CREATE TABLE output_publish_operations (
  account_id uuid NOT NULL,
  run_id uuid NOT NULL,
  operation_id varchar(200) NOT NULL,
  file_id uuid NOT NULL,
  logical_name varchar(255) NOT NULL,
  sha256 char(64) NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  PRIMARY KEY(run_id,operation_id),
  CONSTRAINT uq_output_publish_operations__file UNIQUE(file_id),
  CONSTRAINT fk_output_publish_operations__run FOREIGN KEY(account_id,run_id)
    REFERENCES runs(account_id,run_id) ON DELETE RESTRICT,
  CONSTRAINT ck_output_publish_operations__status CHECK(status IN ('pending','completed'))
);
GRANT SELECT,INSERT,UPDATE ON output_publish_operations TO hpagent_worker;
