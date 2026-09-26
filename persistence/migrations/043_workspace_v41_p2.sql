-- Conversation policies and immutable per-Run candidate membership.
SET search_path TO hpagent, public;
ALTER TABLE account_workspaces ADD COLUMN topology_version bigint NOT NULL DEFAULT 1;
CREATE OR REPLACE FUNCTION bump_workspace_topology() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
BEGIN
  UPDATE account_workspaces SET topology_version=topology_version+1
  WHERE account_id=COALESCE(NEW.account_id,OLD.account_id);
  RETURN COALESCE(NEW,OLD);
END $$;
CREATE TRIGGER tr_workspace_nodes__topology AFTER INSERT OR UPDATE OR DELETE ON workspace_nodes
FOR EACH ROW EXECUTE FUNCTION bump_workspace_topology();

CREATE TABLE resource_policy_versions (
  account_id uuid NOT NULL, subject_kind text NOT NULL CHECK(subject_kind IN ('conversation','task')),
  subject_id uuid NOT NULL, version bigint NOT NULL DEFAULT 1,
  PRIMARY KEY(account_id,subject_kind,subject_id),
  FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT
);
CREATE TABLE resource_grants (
  grant_id uuid PRIMARY KEY, account_id uuid NOT NULL,
  subject_kind text NOT NULL CHECK(subject_kind IN ('conversation','task')),
  subject_id uuid NOT NULL, node_id uuid NOT NULL,
  operation text NOT NULL CHECK(operation IN
    ('list_metadata','read_content','create_child','update_content','delete_entry')),
  recursive boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
  FOREIGN KEY(account_id,subject_kind,subject_id)
    REFERENCES resource_policy_versions(account_id,subject_kind,subject_id) ON DELETE RESTRICT,
  FOREIGN KEY(node_id) REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT
);
CREATE INDEX ix_resource_grants__active ON resource_grants(account_id,subject_kind,subject_id,operation,node_id)
WHERE revoked_at IS NULL;
CREATE TABLE conversation_resource_files (
  account_id uuid NOT NULL, conversation_id uuid NOT NULL, file_id uuid NOT NULL,
  available boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
  PRIMARY KEY(conversation_id,file_id),
  FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id)
    ON DELETE RESTRICT,
  FOREIGN KEY(account_id,file_id) REFERENCES stored_files(account_id,file_id)
    ON DELETE RESTRICT
);
CREATE TABLE run_resource_snapshots (
  run_id uuid PRIMARY KEY, account_id uuid NOT NULL, subject_kind text NOT NULL,
  subject_id uuid NOT NULL, policy_version bigint NOT NULL,
  topology_version bigint NOT NULL, status text NOT NULL CHECK(status IN ('building','ready')),
  candidate_count integer NOT NULL DEFAULT 0, candidate_limit integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
  FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT
);
CREATE TABLE run_resource_candidates (
  run_id uuid NOT NULL, account_id uuid NOT NULL, node_id uuid NOT NULL,
  logical_name varchar(255) NOT NULL, display_name varchar(255) NOT NULL,
  content_type varchar(255), size_bytes bigint,
  fixed_file_id uuid, fixed_revision bigint, fixed_at timestamptz, first_read_at timestamptz,
  PRIMARY KEY(run_id,node_id), UNIQUE(run_id,logical_name),
  FOREIGN KEY(run_id) REFERENCES run_resource_snapshots(run_id) ON DELETE RESTRICT,
  FOREIGN KEY(node_id) REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT,
  FOREIGN KEY(account_id,fixed_file_id) REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT
);
CREATE INDEX ix_run_resource_candidates__page ON run_resource_candidates(run_id,logical_name,node_id);
CREATE TABLE run_resource_access (
  access_id uuid PRIMARY KEY, account_id uuid NOT NULL, run_id uuid NOT NULL, file_id uuid NOT NULL,
  node_id uuid, basis text NOT NULL CHECK(basis IN ('explicit_attachment','workspace_grant','run_output')),
  grant_id uuid, fixed_at timestamptz NOT NULL DEFAULT now(), first_read_at timestamptz,
  FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT,
  FOREIGN KEY(account_id,file_id) REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT,
  FOREIGN KEY(node_id) REFERENCES workspace_nodes(node_id) ON DELETE RESTRICT,
  FOREIGN KEY(grant_id) REFERENCES resource_grants(grant_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX uq_run_resource_access__workspace
ON run_resource_access(run_id,file_id,node_id) WHERE basis='workspace_grant';
CREATE UNIQUE INDEX uq_run_resource_access__attachment
ON run_resource_access(run_id,file_id) WHERE basis='explicit_attachment';
CREATE TABLE run_resource_denials (
  denial_id uuid PRIMARY KEY, account_id uuid NOT NULL, run_id uuid NOT NULL,
  operation text NOT NULL, node_id uuid, file_id uuid, reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT
);
CREATE INDEX ix_run_resource_denials__run ON run_resource_denials(run_id,created_at);
-- NULL node_id entries are limited to explicit attachments and Run output.
ALTER TABLE run_resource_access ADD CONSTRAINT ck_run_resource_access__shape CHECK
  ((basis='workspace_grant' AND node_id IS NOT NULL AND grant_id IS NOT NULL) OR
   (basis<>'workspace_grant' AND node_id IS NULL AND grant_id IS NULL));
GRANT SELECT,INSERT,UPDATE ON conversation_resource_files,resource_policy_versions,resource_grants,
  run_resource_snapshots,run_resource_candidates,run_resource_access TO hpagent_api,hpagent_worker;
GRANT SELECT,INSERT ON run_resource_denials TO hpagent_api,hpagent_worker;
GRANT SELECT ON account_workspaces,workspace_nodes TO hpagent_worker;
GRANT UPDATE(topology_version) ON account_workspaces TO hpagent_api,hpagent_worker;
