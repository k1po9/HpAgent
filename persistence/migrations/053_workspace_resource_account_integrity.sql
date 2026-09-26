SET search_path TO hpagent, public;

ALTER TABLE idempotency_commands DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands ADD CONSTRAINT ck_idempotency_commands__operation
  CHECK(operation IN ('create_conversation','send_message','cancel_run','retry_run',
    'bind_identity','revoke_identity','create_artifact','create_artifact_version',
    'create_upload','create_workspace_upload','delete_file','create_task','trigger_task',
    'update_task_schedule','approve_file_action','reject_file_action'));

ALTER TABLE resource_grants ADD CONSTRAINT uq_resource_grants__account_grant UNIQUE(account_id,grant_id);
ALTER TABLE resource_grants DROP CONSTRAINT resource_grants_node_id_fkey;
ALTER TABLE resource_grants ADD CONSTRAINT fk_resource_grants__account_node
  FOREIGN KEY(account_id,node_id) REFERENCES workspace_nodes(account_id,node_id) ON DELETE RESTRICT;

ALTER TABLE run_resource_snapshots ADD CONSTRAINT uq_run_resource_snapshots__account_run
  UNIQUE(account_id,run_id);
ALTER TABLE run_resource_snapshots ADD CONSTRAINT fk_run_resource_snapshots__subject
  FOREIGN KEY(account_id,subject_kind,subject_id)
  REFERENCES resource_policy_versions(account_id,subject_kind,subject_id) ON DELETE RESTRICT;

ALTER TABLE run_resource_candidates DROP CONSTRAINT run_resource_candidates_run_id_fkey;
ALTER TABLE run_resource_candidates DROP CONSTRAINT run_resource_candidates_node_id_fkey;
ALTER TABLE run_resource_candidates ADD CONSTRAINT fk_run_resource_candidates__account_run
  FOREIGN KEY(account_id,run_id) REFERENCES run_resource_snapshots(account_id,run_id) ON DELETE RESTRICT;
ALTER TABLE run_resource_candidates ADD CONSTRAINT fk_run_resource_candidates__account_node
  FOREIGN KEY(account_id,node_id) REFERENCES workspace_nodes(account_id,node_id) ON DELETE RESTRICT;

ALTER TABLE run_resource_access DROP CONSTRAINT run_resource_access_node_id_fkey;
ALTER TABLE run_resource_access DROP CONSTRAINT run_resource_access_grant_id_fkey;
ALTER TABLE run_resource_access ADD CONSTRAINT fk_run_resource_access__account_node
  FOREIGN KEY(account_id,node_id) REFERENCES workspace_nodes(account_id,node_id) ON DELETE RESTRICT;
ALTER TABLE run_resource_access ADD CONSTRAINT fk_run_resource_access__account_grant
  FOREIGN KEY(account_id,grant_id) REFERENCES resource_grants(account_id,grant_id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION enforce_resource_subject_account() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE r runs%ROWTYPE; g resource_grants%ROWTYPE;
BEGIN
  IF TG_TABLE_NAME='resource_policy_versions' THEN
    IF NEW.subject_kind='conversation' THEN
      PERFORM 1 FROM conversations WHERE account_id=NEW.account_id AND conversation_id=NEW.subject_id;
    ELSE
      PERFORM 1 FROM tasks WHERE account_id=NEW.account_id AND task_id=NEW.subject_id;
    END IF;
    IF NOT FOUND THEN RAISE EXCEPTION 'resource subject belongs to another account'; END IF;
  ELSIF TG_TABLE_NAME='run_resource_snapshots' THEN
    SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
    IF NOT FOUND OR (NEW.subject_kind='conversation' AND r.conversation_id IS DISTINCT FROM NEW.subject_id)
      OR (NEW.subject_kind='task' AND r.task_id IS DISTINCT FROM NEW.subject_id) THEN
      RAISE EXCEPTION 'Run resource subject mismatch';
    END IF;
  ELSE
    IF NEW.basis='workspace_grant' THEN
      SELECT * INTO g FROM resource_grants WHERE account_id=NEW.account_id AND grant_id=NEW.grant_id;
      SELECT * INTO r FROM runs WHERE account_id=NEW.account_id AND run_id=NEW.run_id;
      IF NOT FOUND OR (g.subject_kind='conversation' AND r.conversation_id IS DISTINCT FROM g.subject_id)
        OR (g.subject_kind='task' AND r.task_id IS DISTINCT FROM g.subject_id) THEN
        RAISE EXCEPTION 'resource grant subject mismatch';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_resource_policy_versions__subject BEFORE INSERT OR UPDATE
  ON resource_policy_versions FOR EACH ROW EXECUTE FUNCTION enforce_resource_subject_account();
CREATE TRIGGER tr_run_resource_snapshots__subject BEFORE INSERT OR UPDATE
  ON run_resource_snapshots FOR EACH ROW EXECUTE FUNCTION enforce_resource_subject_account();
CREATE TRIGGER tr_run_resource_access__subject BEFORE INSERT OR UPDATE
  ON run_resource_access FOR EACH ROW EXECUTE FUNCTION enforce_resource_subject_account();
