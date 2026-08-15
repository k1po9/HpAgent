SET search_path TO hpagent, public;

ALTER TABLE idempotency_commands DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands ADD CONSTRAINT ck_idempotency_commands__operation
  CHECK(operation IN (
    'create_conversation','send_message','cancel_run','retry_run',
    'bind_identity','revoke_identity','create_artifact','create_artifact_version'
  ));

CREATE TABLE artifacts (
  artifact_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  source_message_id uuid NOT NULL,
  kind text NOT NULL DEFAULT 'html',
  title varchar(200) NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_artifacts__account_artifact UNIQUE(account_id,artifact_id),
  CONSTRAINT uq_artifacts__account_conversation_artifact
    UNIQUE(account_id,conversation_id,artifact_id),
  CONSTRAINT fk_artifacts__conversations FOREIGN KEY(account_id,conversation_id)
    REFERENCES conversations(account_id,conversation_id) ON DELETE RESTRICT,
  CONSTRAINT fk_artifacts__source_message
    FOREIGN KEY(account_id,conversation_id,source_message_id)
    REFERENCES messages(account_id,conversation_id,message_id) ON DELETE RESTRICT,
  CONSTRAINT ck_artifacts__kind CHECK(kind IN ('html')),
  CONSTRAINT ck_artifacts__title CHECK(length(title) <= 200),
  CONSTRAINT ck_artifacts__timestamps CHECK(updated_at >= created_at)
);
CREATE INDEX ix_artifacts__source_message
  ON artifacts(account_id,source_message_id,created_at,artifact_id);

CREATE TABLE artifact_versions (
  artifact_version_id uuid PRIMARY KEY,
  artifact_id uuid NOT NULL,
  account_id uuid NOT NULL,
  version integer NOT NULL,
  parent_version_id uuid,
  status text NOT NULL DEFAULT 'queued',
  instruction text,
  html text,
  failure_code varchar(100),
  failure_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_artifact_versions__account_version
    UNIQUE(account_id,artifact_id,artifact_version_id),
  CONSTRAINT uq_artifact_versions__version UNIQUE(artifact_id,version),
  CONSTRAINT fk_artifact_versions__artifact FOREIGN KEY(account_id,artifact_id)
    REFERENCES artifacts(account_id,artifact_id) ON DELETE RESTRICT,
  CONSTRAINT fk_artifact_versions__parent
    FOREIGN KEY(account_id,artifact_id,parent_version_id)
    REFERENCES artifact_versions(account_id,artifact_id,artifact_version_id)
    ON DELETE RESTRICT,
  CONSTRAINT ck_artifact_versions__version CHECK(version >= 1),
  CONSTRAINT ck_artifact_versions__status
    CHECK(status IN ('queued','running','completed','failed')),
  CONSTRAINT ck_artifact_versions__not_own_parent
    CHECK(parent_version_id IS NULL OR parent_version_id <> artifact_version_id),
  CONSTRAINT ck_artifact_versions__state CHECK(
    (status='queued' AND html IS NULL AND failure_code IS NULL AND failure_message IS NULL
      AND started_at IS NULL AND completed_at IS NULL) OR
    (status='running' AND html IS NULL AND failure_code IS NULL AND failure_message IS NULL
      AND started_at IS NOT NULL AND completed_at IS NULL) OR
    (status='completed' AND html IS NOT NULL AND failure_code IS NULL AND failure_message IS NULL
      AND started_at IS NOT NULL AND completed_at IS NOT NULL) OR
    (status='failed' AND html IS NULL AND failure_code IS NOT NULL
      AND started_at IS NOT NULL AND completed_at IS NOT NULL)
  ),
  CONSTRAINT ck_artifact_versions__timestamps CHECK(
    updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at)
    AND (completed_at IS NULL OR completed_at >= started_at)
  )
);
CREATE INDEX ix_artifact_versions__list
  ON artifact_versions(account_id,artifact_id,version DESC);

CREATE TABLE artifact_outbox_events (
  artifact_outbox_event_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  artifact_id uuid NOT NULL,
  artifact_version_id uuid NOT NULL,
  event_type text NOT NULL,
  business_key varchar(300) NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  attempt_count integer NOT NULL DEFAULT 0,
  available_at timestamptz NOT NULL DEFAULT now(),
  locked_at timestamptz,
  locked_by varchar(200),
  last_error_code varchar(100),
  last_error_message text,
  processed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_artifact_outbox__business_key UNIQUE(business_key),
  CONSTRAINT fk_artifact_outbox__artifact
    FOREIGN KEY(account_id,conversation_id,artifact_id)
    REFERENCES artifacts(account_id,conversation_id,artifact_id) ON DELETE RESTRICT,
  CONSTRAINT fk_artifact_outbox__version
    FOREIGN KEY(account_id,artifact_id,artifact_version_id)
    REFERENCES artifact_versions(account_id,artifact_id,artifact_version_id) ON DELETE RESTRICT,
  CONSTRAINT ck_artifact_outbox__event_type CHECK(event_type='start_artifact_build'),
  CONSTRAINT ck_artifact_outbox__status
    CHECK(status IN ('pending','processing','processed','dead_letter')),
  CONSTRAINT ck_artifact_outbox__attempt_count CHECK(attempt_count >= 0),
  CONSTRAINT ck_artifact_outbox__lease_shape CHECK(
    (status='processing' AND locked_at IS NOT NULL AND locked_by IS NOT NULL) OR
    (status<>'processing' AND locked_at IS NULL AND locked_by IS NULL)
  ),
  CONSTRAINT ck_artifact_outbox__processed_shape CHECK(
    (status='processed' AND processed_at IS NOT NULL) OR
    (status<>'processed' AND processed_at IS NULL)
  )
);
CREATE INDEX ix_artifact_outbox__claim ON artifact_outbox_events(
  available_at,created_at,artifact_outbox_event_id
) WHERE status='pending';
CREATE INDEX ix_artifact_outbox__expired_lease
  ON artifact_outbox_events(locked_at,artifact_outbox_event_id) WHERE status='processing';

GRANT SELECT, INSERT ON artifacts, artifact_versions, artifact_outbox_events TO hpagent_api;
GRANT SELECT, UPDATE ON artifacts, artifact_versions, artifact_outbox_events
  TO hpagent_worker;
