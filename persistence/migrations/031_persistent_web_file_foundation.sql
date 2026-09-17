-- F4.1: account-owned persistent destinations and recoverable approval binding.
SET search_path TO hpagent, public;

ALTER TABLE stored_files ADD CONSTRAINT uq_stored_files__account_file
  UNIQUE(account_id,file_id);

ALTER TABLE file_action_approvals
  ADD COLUMN intent jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN execution_id varchar(255),
  ADD COLUMN execution_fencing_token bigint,
  ADD COLUMN execution_bound_at timestamptz;
-- Preserve already-consumed grants from 029 as their own legacy execution.
UPDATE file_action_approvals SET
  execution_id=operation_id,
  execution_fencing_token=1,
  execution_bound_at=COALESCE(consumed_at,updated_at)
WHERE status='consumed';
ALTER TABLE file_action_approvals ADD CONSTRAINT ck_file_action_approvals__intent
  CHECK(jsonb_typeof(intent)='object');
ALTER TABLE file_action_approvals ADD CONSTRAINT ck_file_action_approvals__execution_binding CHECK(
  (status <> 'consumed' AND execution_id IS NULL AND execution_fencing_token IS NULL
    AND execution_bound_at IS NULL)
  OR
  (status='consumed' AND execution_id=operation_id AND execution_fencing_token >= 1
    AND execution_bound_at IS NOT NULL)
);

CREATE TABLE persistent_file_destinations (
  destination_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  logical_path varchar(500) NOT NULL,
  current_revision bigint NOT NULL,
  current_file_id uuid NOT NULL,
  current_sha256 char(64) NOT NULL,
  last_operation_id varchar(255) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_persistent_file_destinations__path UNIQUE(account_id,logical_path),
  CONSTRAINT uq_persistent_file_destinations__account_destination
    UNIQUE(account_id,destination_id),
  CONSTRAINT fk_persistent_file_destinations__account FOREIGN KEY(account_id)
    REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT fk_persistent_file_destinations__file FOREIGN KEY(account_id,current_file_id)
    REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT,
  CONSTRAINT ck_persistent_file_destinations__revision CHECK(current_revision >= 1),
  CONSTRAINT ck_persistent_file_destinations__sha CHECK(current_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_persistent_file_destinations__path CHECK(
    length(btrim(logical_path)) BETWEEN 1 AND 500 AND
    logical_path !~ '(^/|(^|/)\.\.(/|$)|[\\])')
);

CREATE TABLE persistent_file_revisions (
  account_id uuid NOT NULL,
  destination_id uuid NOT NULL,
  revision bigint NOT NULL,
  file_id uuid NOT NULL,
  sha256 char(64) NOT NULL,
  operation_id varchar(255) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(destination_id,revision),
  CONSTRAINT uq_persistent_file_revisions__file UNIQUE(file_id),
  CONSTRAINT uq_persistent_file_revisions__operation UNIQUE(destination_id,operation_id),
  CONSTRAINT fk_persistent_file_revisions__destination FOREIGN KEY(account_id,destination_id)
    REFERENCES persistent_file_destinations(account_id,destination_id) ON DELETE RESTRICT,
  CONSTRAINT fk_persistent_file_revisions__file FOREIGN KEY(account_id,file_id)
    REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT,
  CONSTRAINT ck_persistent_file_revisions__revision CHECK(revision >= 1),
  CONSTRAINT ck_persistent_file_revisions__sha CHECK(sha256 ~ '^[0-9a-f]{64}$')
);

GRANT SELECT ON persistent_file_destinations,persistent_file_revisions TO hpagent_api;
GRANT SELECT,INSERT,UPDATE ON persistent_file_destinations,persistent_file_revisions
  TO hpagent_worker;
