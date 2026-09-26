-- Workspace v4.1 P3: entry-owned location and independent immutable version chains.
SET search_path TO hpagent, public;

ALTER TABLE persistent_file_revisions
  DROP CONSTRAINT uq_persistent_file_revisions__file;
ALTER TABLE persistent_file_destinations DROP COLUMN last_operation_id;

ALTER TABLE persistent_file_revisions ADD CONSTRAINT
  uq_persistent_file_revisions__current
  UNIQUE(account_id,destination_id,revision,file_id,sha256);
ALTER TABLE persistent_file_destinations ADD CONSTRAINT
  fk_persistent_file_destinations__current_revision
  FOREIGN KEY(account_id,destination_id,current_revision,current_file_id,current_sha256)
  REFERENCES persistent_file_revisions(account_id,destination_id,revision,file_id,sha256)
  DEFERRABLE INITIALLY DEFERRED;

CREATE OR REPLACE FUNCTION enforce_persistent_revision_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'persistent revision is immutable';
END $$;
CREATE TRIGGER tr_persistent_revisions__immutable BEFORE UPDATE OR DELETE
  ON persistent_file_revisions FOR EACH ROW
  EXECUTE FUNCTION enforce_persistent_revision_immutable();
CREATE OR REPLACE FUNCTION enforce_persistent_revision_hash() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM stored_files WHERE account_id=NEW.account_id
    AND file_id=NEW.file_id AND status='ready' AND sha256=NEW.sha256) THEN
    RAISE EXCEPTION 'revision file hash does not match ready object';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_persistent_revisions__hash BEFORE INSERT
  ON persistent_file_revisions FOR EACH ROW
  EXECUTE FUNCTION enforce_persistent_revision_hash();
ALTER TABLE persistent_file_revisions ADD CONSTRAINT
  uq_persistent_file_revisions__result
  UNIQUE(account_id,destination_id,revision,file_id);
ALTER TABLE workspace_nodes ADD CONSTRAINT uq_workspace_nodes__account_node
  UNIQUE(account_id,node_id);

-- A committed operation keeps its original result even after the destination advances.
CREATE TABLE workspace_version_operations (
  account_id uuid NOT NULL,
  operation_id varchar(200) NOT NULL,
  intent_sha256 char(64) NOT NULL,
  node_id uuid NOT NULL,
  destination_id uuid NOT NULL,
  revision bigint NOT NULL,
  file_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,operation_id),
  FOREIGN KEY(account_id,destination_id,revision,file_id)
    REFERENCES persistent_file_revisions(account_id,destination_id,revision,file_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(account_id,node_id) REFERENCES workspace_nodes(account_id,node_id)
    ON DELETE RESTRICT
);

-- The only permitted content change on a node is an in-place immutable-entry upgrade.
CREATE OR REPLACE FUNCTION enforce_workspace_node() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE p workspace_nodes%ROWTYPE; f stored_files%ROWTYPE; recursive_hit boolean;
BEGIN
  PERFORM 1 FROM account_workspaces WHERE account_id=NEW.account_id
    AND workspace_id=NEW.workspace_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Workspace unavailable'; END IF;
  IF TG_OP='UPDATE' AND (
    OLD.account_id,OLD.workspace_id,OLD.kind) IS DISTINCT FROM
    (NEW.account_id,NEW.workspace_id,NEW.kind) THEN
    RAISE EXCEPTION 'Workspace node identity is immutable';
  END IF;
  IF TG_OP='UPDATE' AND (
    OLD.file_id IS DISTINCT FROM NEW.file_id OR
    OLD.destination_id IS DISTINCT FROM NEW.destination_id) AND NOT (
      OLD.kind='file' AND OLD.file_id IS NOT NULL AND OLD.destination_id IS NULL AND
      NEW.file_id IS NULL AND NEW.destination_id IS NOT NULL AND
      NEW.deleted_at IS NULL AND OLD.deleted_at IS NULL AND
      EXISTS(SELECT 1 FROM persistent_file_revisions r
        WHERE r.account_id=NEW.account_id AND r.destination_id=NEW.destination_id
        AND r.revision=1 AND r.file_id=OLD.file_id)
    ) THEN RAISE EXCEPTION 'Workspace content identity is immutable'; END IF;
  IF TG_OP='UPDATE' AND OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
    RAISE EXCEPTION 'Workspace tombstone cannot be revived'; END IF;
  IF TG_OP='UPDATE' AND OLD.parent_id IS NULL AND NEW.parent_id IS NOT NULL THEN
    RAISE EXCEPTION 'Workspace root is immutable'; END IF;
  IF NEW.parent_id IS NULL THEN
    IF TG_OP='UPDATE' AND (NEW.deleted_at IS NOT NULL OR NEW.name<>'' OR NEW.name_key<>'') THEN
      RAISE EXCEPTION 'Workspace root is immutable'; END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO p FROM workspace_nodes WHERE account_id=NEW.account_id
    AND workspace_id=NEW.workspace_id AND node_id=NEW.parent_id;
  IF NOT FOUND OR p.kind<>'directory' OR p.deleted_at IS NOT NULL THEN
    RAISE EXCEPTION 'Parent must be an active directory in this Workspace'; END IF;
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
      RAISE EXCEPTION 'Directory is not empty'; END IF;
  END IF;
  IF NEW.kind='file' AND NEW.file_id IS NOT NULL AND NEW.deleted_at IS NULL THEN
    SELECT * INTO f FROM stored_files WHERE account_id=NEW.account_id
      AND file_id=NEW.file_id FOR UPDATE;
    IF NOT FOUND OR f.status<>'ready' THEN
      RAISE EXCEPTION 'Workspace file is unavailable'; END IF;
  END IF;
  RETURN NEW;
END $$;

GRANT SELECT,INSERT ON workspace_version_operations TO hpagent_api,hpagent_worker;
GRANT INSERT,UPDATE ON persistent_file_destinations TO hpagent_api;
GRANT INSERT ON persistent_file_revisions TO hpagent_api;
