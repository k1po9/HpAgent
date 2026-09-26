-- Workspace v4.1 P1: one account-owned tree and zero-copy immutable entries.
SET search_path TO hpagent, public;

CREATE TABLE account_workspaces (
  workspace_id uuid PRIMARY KEY,
  account_id uuid NOT NULL UNIQUE REFERENCES accounts(account_id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_account_workspaces__scope UNIQUE(account_id,workspace_id)
);

-- P0 prerequisite correction: source identity must be interpretable.
ALTER TABLE stored_files ADD COLUMN source_workspace_id uuid;
ALTER TABLE stored_files ADD CONSTRAINT fk_stored_files__source_workspace
  FOREIGN KEY(account_id,source_workspace_id)
  REFERENCES account_workspaces(account_id,workspace_id) ON DELETE RESTRICT;
ALTER TABLE stored_files ADD CONSTRAINT ck_stored_files__source_shape CHECK(
  (purpose='input' AND source_run_id IS NULL AND
    num_nonnulls(conversation_id,source_workspace_id)=1) OR
  (purpose='output' AND source_run_id IS NOT NULL AND source_workspace_id IS NULL)
);
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
    NEW.source_workspace_id IS DISTINCT FROM OLD.source_workspace_id OR
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

CREATE TABLE workspace_nodes (
  node_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  workspace_id uuid NOT NULL,
  parent_id uuid,
  kind text NOT NULL CHECK(kind IN ('directory','file')),
  name varchar(255) NOT NULL,
  name_key varchar(255) NOT NULL,
  file_id uuid,
  destination_id uuid,
  deleted_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_workspace_nodes__scope UNIQUE(account_id,workspace_id,node_id),
  CONSTRAINT fk_workspace_nodes__workspace FOREIGN KEY(account_id,workspace_id)
    REFERENCES account_workspaces(account_id,workspace_id) ON DELETE RESTRICT,
  CONSTRAINT fk_workspace_nodes__parent FOREIGN KEY(account_id,workspace_id,parent_id)
    REFERENCES workspace_nodes(account_id,workspace_id,node_id) ON DELETE RESTRICT,
  CONSTRAINT fk_workspace_nodes__file FOREIGN KEY(account_id,file_id)
    REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT,
  CONSTRAINT fk_workspace_nodes__destination FOREIGN KEY(account_id,destination_id)
    REFERENCES persistent_file_destinations(account_id,destination_id) ON DELETE RESTRICT,
  CONSTRAINT ck_workspace_nodes__shape CHECK(
    (kind='directory' AND file_id IS NULL AND destination_id IS NULL) OR
    (kind='file' AND num_nonnulls(file_id,destination_id)=1)),
  CONSTRAINT ck_workspace_nodes__name CHECK(
    length(name) BETWEEN 1 AND 255 AND length(name_key) BETWEEN 1 AND 255),
  CONSTRAINT ck_workspace_nodes__root CHECK(parent_id IS NOT NULL OR
    (kind='directory' AND name='' AND name_key=''))
);
-- The root is represented by an empty display name, never by a user child.
ALTER TABLE workspace_nodes DROP CONSTRAINT ck_workspace_nodes__name;
ALTER TABLE workspace_nodes ADD CONSTRAINT ck_workspace_nodes__name CHECK(
  (parent_id IS NULL AND name='' AND name_key='') OR
  (parent_id IS NOT NULL AND length(name) BETWEEN 1 AND 255
   AND length(name_key) BETWEEN 1 AND 255));
CREATE UNIQUE INDEX uq_workspace_nodes__root ON workspace_nodes(workspace_id)
  WHERE parent_id IS NULL;
CREATE UNIQUE INDEX uq_workspace_nodes__siblings ON workspace_nodes(
  workspace_id,parent_id,name_key) WHERE deleted_at IS NULL;
CREATE INDEX ix_workspace_nodes__file ON workspace_nodes(account_id,file_id)
  WHERE deleted_at IS NULL AND file_id IS NOT NULL;
CREATE UNIQUE INDEX uq_workspace_nodes__active_destination ON workspace_nodes(destination_id)
  WHERE deleted_at IS NULL AND destination_id IS NOT NULL;

CREATE TABLE workspace_save_operations (
  account_id uuid NOT NULL,
  operation_id varchar(200) NOT NULL,
  intent_sha256 char(64) NOT NULL,
  node_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,operation_id),
  CONSTRAINT fk_workspace_save_operations__account FOREIGN KEY(account_id)
    REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT fk_workspace_save_operations__node FOREIGN KEY(node_id)
    REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION enforce_workspace_node() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE p workspace_nodes%ROWTYPE; f stored_files%ROWTYPE; recursive_hit boolean;
BEGIN
  -- Shared tree lock serializes even direct SQL moves and prevents write skew.
  PERFORM 1 FROM account_workspaces WHERE account_id=NEW.account_id
    AND workspace_id=NEW.workspace_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Workspace unavailable'; END IF;
  IF TG_OP='UPDATE' AND (OLD.account_id,OLD.workspace_id,OLD.kind,OLD.file_id,OLD.destination_id)
      IS DISTINCT FROM (NEW.account_id,NEW.workspace_id,NEW.kind,NEW.file_id,NEW.destination_id) THEN
    RAISE EXCEPTION 'Workspace node identity is immutable';
  END IF;
  IF TG_OP='UPDATE' AND OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
    RAISE EXCEPTION 'Workspace tombstone cannot be revived';
  END IF;
  IF TG_OP='UPDATE' AND OLD.parent_id IS NULL AND NEW.parent_id IS NOT NULL THEN
    RAISE EXCEPTION 'Workspace root is immutable';
  END IF;
  IF NEW.parent_id IS NULL THEN
    IF TG_OP='UPDATE' AND (NEW.deleted_at IS NOT NULL OR NEW.name<>'' OR NEW.name_key<>'') THEN
      RAISE EXCEPTION 'Workspace root is immutable';
    END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO p FROM workspace_nodes WHERE account_id=NEW.account_id
    AND workspace_id=NEW.workspace_id AND node_id=NEW.parent_id;
  IF NOT FOUND OR p.kind<>'directory' OR p.deleted_at IS NOT NULL THEN
    RAISE EXCEPTION 'Parent must be an active directory in this Workspace';
  END IF;
  IF NEW.node_id=NEW.parent_id THEN RAISE EXCEPTION 'Workspace cycle'; END IF;
  IF TG_OP='UPDATE' AND NEW.parent_id IS DISTINCT FROM OLD.parent_id THEN
    WITH RECURSIVE ancestors AS (
      SELECT node_id,parent_id FROM workspace_nodes WHERE node_id=NEW.parent_id
      UNION ALL SELECT n.node_id,n.parent_id FROM workspace_nodes n
        JOIN ancestors a ON n.node_id=a.parent_id
    ) SELECT true INTO recursive_hit FROM ancestors WHERE node_id=NEW.node_id LIMIT 1;
    IF recursive_hit THEN RAISE EXCEPTION 'Workspace cycle'; END IF;
  END IF;
  IF NEW.kind='directory' AND NEW.deleted_at IS NOT NULL
      AND (TG_OP='INSERT' OR OLD.deleted_at IS NULL) THEN
    IF EXISTS (SELECT 1 FROM workspace_nodes child WHERE child.parent_id=NEW.node_id
      AND child.deleted_at IS NULL) THEN
      RAISE EXCEPTION 'Directory is not empty';
    END IF;
  END IF;
  IF NEW.kind='file' AND NEW.file_id IS NOT NULL AND NEW.deleted_at IS NULL THEN
    SELECT * INTO f FROM stored_files WHERE account_id=NEW.account_id
      AND file_id=NEW.file_id FOR UPDATE;
    IF NOT FOUND OR f.status<>'ready' THEN
      RAISE EXCEPTION 'Workspace file is unavailable';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_workspace_nodes__shape BEFORE INSERT OR UPDATE ON workspace_nodes
  FOR EACH ROW EXECUTE FUNCTION enforce_workspace_node();

-- Destinations retain revision history for GC and future versioned entries,
-- but no longer own a user path. Entries own all visible paths.
ALTER TABLE persistent_file_destinations
  DROP CONSTRAINT uq_persistent_file_destinations__path;
ALTER TABLE persistent_file_destinations
  DROP CONSTRAINT ck_persistent_file_destinations__path;
ALTER TABLE persistent_file_destinations DROP COLUMN logical_path;

-- Every new durable reference competes with GC on the same stored_files row.
CREATE OR REPLACE FUNCTION lock_file_for_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE f stored_files%ROWTYPE; target_file_id uuid;
BEGIN
  IF TG_TABLE_NAME='persistent_file_destinations' THEN
    target_file_id := NEW.current_file_id;
  ELSE
    target_file_id := NEW.file_id;
  END IF;
  SELECT * INTO f FROM stored_files WHERE account_id=NEW.account_id
    AND file_id=target_file_id FOR UPDATE;
  IF NOT FOUND OR f.status<>'ready' THEN RAISE EXCEPTION 'referenced file is unavailable'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_persistent_revisions__file_ready BEFORE INSERT ON persistent_file_revisions
  FOR EACH ROW EXECUTE FUNCTION lock_file_for_reference();
CREATE TRIGGER tr_persistent_destinations__file_ready
  BEFORE INSERT OR UPDATE OF current_file_id ON persistent_file_destinations
  FOR EACH ROW EXECUTE FUNCTION lock_file_for_reference();

CREATE OR REPLACE FUNCTION enforce_run_file_account() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE r runs%ROWTYPE; f stored_files%ROWTYPE;
BEGIN
  SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
  IF NOT FOUND OR NEW.conversation_id IS DISTINCT FROM r.conversation_id THEN
    RAISE EXCEPTION 'Run file context does not match Run'; END IF;
  SELECT * INTO f FROM stored_files WHERE account_id=NEW.account_id
    AND file_id=NEW.file_id FOR UPDATE;
  IF NOT FOUND OR f.status<>'ready' THEN RAISE EXCEPTION 'Run file must be ready'; END IF;
  IF NEW.direction='output' AND (f.purpose<>'output' OR
      f.source_run_id IS DISTINCT FROM NEW.run_id) THEN
    RAISE EXCEPTION 'published output must originate from this Run'; END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION enforce_file_binding_shape() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE m messages%ROWTYPE; f stored_files%ROWTYPE;
BEGIN
  SELECT * INTO m FROM messages WHERE account_id=NEW.account_id AND message_id=NEW.message_id;
  SELECT * INTO f FROM stored_files WHERE account_id=NEW.account_id
    AND file_id=NEW.file_id FOR UPDATE;
  IF NOT FOUND OR f.status<>'ready' THEN RAISE EXCEPTION 'only ready files may be bound'; END IF;
  IF NEW.role='input' AND m.role<>'user' THEN
    RAISE EXCEPTION 'input files require a user message'; END IF;
  IF NEW.role='output' AND (m.role<>'assistant' OR m.status<>'completed' OR
      f.purpose<>'output') THEN
    RAISE EXCEPTION 'output files require a completed assistant message'; END IF;
  RETURN NEW;
END $$;

GRANT SELECT,INSERT,UPDATE ON account_workspaces,workspace_nodes,workspace_save_operations
  TO hpagent_api, hpagent_worker;
