-- Phase A initial schema.  This migration is deliberately greenfield: it never
-- touches the legacy session/Redis/Temporal/Hindsight stores.
CREATE SCHEMA IF NOT EXISTS hpagent;
SET search_path TO hpagent, public;
-- The migration role provisions these no-login-by-default application roles in a
-- greenfield installation. Deployment may pre-create them with managed secrets.
DO $$ BEGIN
  CREATE ROLE hpagent_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE ROLE hpagent_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE accounts (
  account_id uuid PRIMARY KEY, status text NOT NULL DEFAULT 'active', version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_accounts__status CHECK (status IN ('active','disabled')),
  CONSTRAINT ck_accounts__version CHECK (version >= 1), CONSTRAINT ck_accounts__timestamps CHECK (updated_at >= created_at)
);
CREATE TABLE identity_bindings (
  identity_binding_id uuid PRIMARY KEY, account_id uuid NOT NULL, provider text NOT NULL,
  external_subject_id text NOT NULL, normalized_subject_id text NOT NULL, status text NOT NULL DEFAULT 'active',
  verified_at timestamptz, revoked_at timestamptz, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  version bigint NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_identity_bindings__accounts FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT uq_identity_bindings__account_binding UNIQUE (account_id, identity_binding_id),
  CONSTRAINT ck_identity_bindings__provider CHECK (provider IN ('web','qq')),
  CONSTRAINT ck_identity_bindings__status CHECK (status IN ('active','revoked')),
  CONSTRAINT ck_identity_bindings__subject_not_empty CHECK (length(external_subject_id) BETWEEN 1 AND 2048 AND length(normalized_subject_id) BETWEEN 1 AND 512),
  CONSTRAINT ck_identity_bindings__active_verified CHECK (status <> 'active' OR (verified_at IS NOT NULL AND revoked_at IS NULL)),
  CONSTRAINT ck_identity_bindings__revoked_at CHECK (status <> 'revoked' OR revoked_at IS NOT NULL),
  CONSTRAINT ck_identity_bindings__metadata_object CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE UNIQUE INDEX uq_identity_bindings__active_subject ON identity_bindings(provider, normalized_subject_id) WHERE status='active';
CREATE INDEX ix_identity_bindings__account ON identity_bindings(account_id,status,provider);

CREATE TABLE web_auth_sessions (
  web_auth_session_id uuid PRIMARY KEY, account_id uuid NOT NULL, identity_binding_id uuid NOT NULL,
  token_hash bytea NOT NULL, csrf_secret_hash bytea NOT NULL, expires_at timestamptz NOT NULL,
  idle_expires_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_web_auth_sessions__accounts FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT fk_web_auth_sessions__identity_bindings FOREIGN KEY (account_id,identity_binding_id) REFERENCES identity_bindings(account_id,identity_binding_id) ON DELETE RESTRICT,
  CONSTRAINT uq_web_auth_sessions__token_hash UNIQUE(token_hash),
  CONSTRAINT ck_web_auth_sessions__expiry CHECK (idle_expires_at <= expires_at AND expires_at > created_at)
);
CREATE INDEX ix_web_auth_sessions__account ON web_auth_sessions(account_id,created_at DESC);

CREATE TABLE conversations (
  conversation_id uuid PRIMARY KEY, account_id uuid NOT NULL, title varchar(200) NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'active', last_message_seq bigint NOT NULL DEFAULT 0, metadata_version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), archived_at timestamptz,
  CONSTRAINT fk_conversations__accounts FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT uq_conversations__account_conversation UNIQUE(account_id,conversation_id),
  CONSTRAINT ck_conversations__status CHECK(status IN ('active','archived')),
  CONSTRAINT ck_conversations__last_message_seq CHECK(last_message_seq >= 0),
  CONSTRAINT ck_conversations__metadata_version CHECK(metadata_version >= 1),
  CONSTRAINT ck_conversations__archive_state CHECK((status='archived')=(archived_at IS NOT NULL))
);
CREATE INDEX ix_conversations__account_list ON conversations(account_id,updated_at DESC,conversation_id DESC);

CREATE TABLE messages (
  message_id uuid PRIMARY KEY, account_id uuid NOT NULL, conversation_id uuid NOT NULL, role text NOT NULL,
  status text NOT NULL, content text, sequence bigint NOT NULL, client_request_id uuid, produced_by_run_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
  CONSTRAINT uq_messages__account_conversation_message UNIQUE(account_id,conversation_id,message_id),
  CONSTRAINT uq_messages__conversation_sequence UNIQUE(conversation_id,sequence),
  CONSTRAINT fk_messages__conversations FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id) ON DELETE RESTRICT,
  CONSTRAINT ck_messages__sequence CHECK(sequence >= 1), CONSTRAINT ck_messages__role CHECK(role IN ('user','assistant')),
  CONSTRAINT ck_messages__status CHECK(status IN ('accepted','pending','completed','failed','aborted')),
  CONSTRAINT ck_messages__shape CHECK((role='user' AND status='accepted' AND client_request_id IS NOT NULL AND produced_by_run_id IS NULL AND content IS NOT NULL AND completed_at IS NULL) OR (role='assistant' AND status IN ('pending','completed','failed','aborted') AND client_request_id IS NULL AND produced_by_run_id IS NOT NULL AND ((status='pending' AND completed_at IS NULL) OR (status IN ('completed','failed','aborted') AND completed_at IS NOT NULL)) AND (status <> 'completed' OR content IS NOT NULL))),
  CONSTRAINT ck_messages__completed_at CHECK(completed_at IS NULL OR completed_at >= created_at)
);
CREATE UNIQUE INDEX uq_messages__account_client_request ON messages(account_id,client_request_id) WHERE client_request_id IS NOT NULL;
CREATE UNIQUE INDEX uq_messages__produced_by_run ON messages(produced_by_run_id) WHERE produced_by_run_id IS NOT NULL;
CREATE INDEX ix_messages__conversation_history ON messages(account_id,conversation_id,sequence DESC);
CREATE INDEX ix_messages__context ON messages(account_id,conversation_id,sequence) WHERE (role='user' AND status='accepted') OR (role='assistant' AND status='completed');

CREATE TABLE sessions (
  session_id uuid PRIMARY KEY, account_id uuid NOT NULL, conversation_id uuid NOT NULL, sequence bigint NOT NULL,
  status text NOT NULL DEFAULT 'active', predecessor_session_id uuid, summary text, workspace_ref text, version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(), archived_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_sessions__account_conversation_session UNIQUE(account_id,conversation_id,session_id),
  CONSTRAINT uq_sessions__conversation_sequence UNIQUE(conversation_id,sequence),
  CONSTRAINT fk_sessions__conversations FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id) ON DELETE RESTRICT,
  CONSTRAINT fk_sessions__predecessor FOREIGN KEY(account_id,conversation_id,predecessor_session_id) REFERENCES sessions(account_id,conversation_id,session_id) ON DELETE RESTRICT,
  CONSTRAINT ck_sessions__sequence CHECK(sequence>=1), CONSTRAINT ck_sessions__status CHECK(status IN ('active','archiving','archived','failed')),
  CONSTRAINT ck_sessions__not_own_predecessor CHECK(predecessor_session_id IS NULL OR predecessor_session_id<>session_id),
  CONSTRAINT ck_sessions__archived_at CHECK((status='archived')=(archived_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_sessions__one_active_per_conversation ON sessions(conversation_id) WHERE status='active';
CREATE INDEX ix_sessions__conversation_history ON sessions(account_id,conversation_id,sequence DESC);

CREATE TABLE runs (
  run_id uuid PRIMARY KEY, account_id uuid NOT NULL, conversation_id uuid NOT NULL, session_id uuid NOT NULL,
  trigger_message_id uuid NOT NULL, retry_of_run_id uuid, workflow_id varchar(200) NOT NULL, context_message_seq bigint NOT NULL,
  status text NOT NULL DEFAULT 'queued', failure_code varchar(100), failure_message text, version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_runs__account_conversation_run UNIQUE(account_id,conversation_id,run_id),
  CONSTRAINT uq_runs__account_conversation_run_workflow UNIQUE(account_id,conversation_id,run_id,workflow_id), CONSTRAINT uq_runs__workflow_id UNIQUE(workflow_id),
  CONSTRAINT fk_runs__conversations FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id) ON DELETE RESTRICT,
  CONSTRAINT fk_runs__sessions FOREIGN KEY(account_id,conversation_id,session_id) REFERENCES sessions(account_id,conversation_id,session_id) ON DELETE RESTRICT,
  CONSTRAINT fk_runs__trigger_messages FOREIGN KEY(account_id,conversation_id,trigger_message_id) REFERENCES messages(account_id,conversation_id,message_id) ON DELETE RESTRICT,
  CONSTRAINT fk_runs__retry_source FOREIGN KEY(account_id,conversation_id,retry_of_run_id) REFERENCES runs(account_id,conversation_id,run_id) ON DELETE RESTRICT,
  CONSTRAINT ck_runs__status CHECK(status IN ('queued','running','cancelling','completed','failed','cancelled')),
  CONSTRAINT ck_runs__context_message_seq CHECK(context_message_seq>=1), CONSTRAINT ck_runs__not_own_retry CHECK(retry_of_run_id IS NULL OR retry_of_run_id<>run_id),
  CONSTRAINT ck_runs__timestamps CHECK((started_at IS NULL OR started_at>=created_at) AND (finished_at IS NULL OR finished_at>=created_at) AND (status IN ('completed','failed','cancelled'))=(finished_at IS NOT NULL)),
  CONSTRAINT ck_runs__failure CHECK((status='failed' AND failure_code IS NOT NULL) OR (status <> 'failed' AND failure_code IS NULL AND failure_message IS NULL))
);
ALTER TABLE messages ADD CONSTRAINT fk_messages__producing_runs FOREIGN KEY(account_id,conversation_id,produced_by_run_id) REFERENCES runs(account_id,conversation_id,run_id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX uq_runs__one_active_per_conversation ON runs(conversation_id) WHERE status IN ('queued','running','cancelling');
CREATE UNIQUE INDEX uq_runs__one_direct_retry ON runs(retry_of_run_id) WHERE retry_of_run_id IS NOT NULL;
CREATE INDEX ix_runs__conversation_history ON runs(account_id,conversation_id,created_at DESC,run_id DESC);
CREATE INDEX ix_runs__reconcile ON runs(updated_at,run_id) WHERE status IN ('queued','running','cancelling');

CREATE TABLE workflow_executions (
  workflow_execution_id uuid PRIMARY KEY, account_id uuid NOT NULL, conversation_id uuid NOT NULL, run_id uuid NOT NULL,
  workflow_id varchar(200) NOT NULL, temporal_run_id uuid, execution_sequence bigint NOT NULL DEFAULT 1, is_current boolean NOT NULL DEFAULT true,
  status text NOT NULL DEFAULT 'scheduled', version bigint NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz, closed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_workflow_executions__account_conversation_execution UNIQUE(account_id,conversation_id,workflow_execution_id),
  CONSTRAINT uq_workflow_executions__run_sequence UNIQUE(run_id,execution_sequence),
  CONSTRAINT fk_workflow_executions__runs FOREIGN KEY(account_id,conversation_id,run_id,workflow_id) REFERENCES runs(account_id,conversation_id,run_id,workflow_id) ON DELETE RESTRICT,
  CONSTRAINT ck_workflow_executions__sequence CHECK(execution_sequence>=1),
  CONSTRAINT ck_workflow_executions__status CHECK(status IN ('scheduled','running','cancel_requested','completed','failed','cancelled','terminated','timed_out')),
  CONSTRAINT ck_workflow_executions__closed_at CHECK((status IN ('completed','failed','cancelled','terminated','timed_out'))=(closed_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_workflow_executions__current_per_run ON workflow_executions(run_id) WHERE is_current;

CREATE TABLE idempotency_commands (
  idempotency_command_id uuid PRIMARY KEY, account_id uuid NOT NULL, operation text NOT NULL, idempotency_key varchar(128) NOT NULL,
  request_hash bytea NOT NULL, status text NOT NULL DEFAULT 'in_progress', resource_type text, resource_id uuid, response_status smallint, response_body jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz, expires_at timestamptz NOT NULL,
  CONSTRAINT fk_idempotency_commands__accounts FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT uq_idempotency_commands__scope UNIQUE(account_id,operation,idempotency_key),
  CONSTRAINT ck_idempotency_commands__operation CHECK(operation IN ('create_conversation','send_message','cancel_run','retry_run','bind_identity','revoke_identity')),
  CONSTRAINT ck_idempotency_commands__status CHECK(status IN ('in_progress','completed')),
  CONSTRAINT ck_idempotency_commands__response_shape CHECK((status='in_progress' AND completed_at IS NULL AND response_status IS NULL AND response_body IS NULL) OR (status='completed' AND completed_at IS NOT NULL AND response_status IS NOT NULL AND response_body IS NOT NULL)),
  CONSTRAINT ck_idempotency_commands__response_status CHECK(response_status IS NULL OR response_status BETWEEN 100 AND 599),
  CONSTRAINT ck_idempotency_commands__response_body CHECK(response_body IS NULL OR jsonb_typeof(response_body)='object'), CONSTRAINT ck_idempotency_commands__expiry CHECK(expires_at>created_at)
);

CREATE TABLE outbox_events (
  outbox_event_id uuid PRIMARY KEY, account_id uuid NOT NULL, event_type text NOT NULL, business_key varchar(300) NOT NULL,
  conversation_id uuid NOT NULL, run_id uuid NOT NULL, payload_version smallint NOT NULL DEFAULT 1, payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'pending', attempt_count integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(), locked_at timestamptz, locked_by varchar(200),
  last_error_code varchar(100), last_error_message text, processed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_outbox_events__accounts FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT fk_outbox_events__runs FOREIGN KEY(account_id,conversation_id,run_id) REFERENCES runs(account_id,conversation_id,run_id) ON DELETE RESTRICT,
  CONSTRAINT uq_outbox_events__business_key UNIQUE(business_key), CONSTRAINT ck_outbox_events__event_type CHECK(event_type IN ('start_run','cancel_run','retain_memory','publish_terminal_event')),
  CONSTRAINT ck_outbox_events__status CHECK(status IN ('pending','processing','processed','dead_letter')), CONSTRAINT ck_outbox_events__attempt_count CHECK(attempt_count>=0),
  CONSTRAINT ck_outbox_events__payload CHECK(payload_version>=1 AND jsonb_typeof(payload)='object'),
  CONSTRAINT ck_outbox_events__lease_shape CHECK((status='processing' AND locked_at IS NOT NULL AND locked_by IS NOT NULL) OR (status<>'processing' AND locked_at IS NULL AND locked_by IS NULL)),
  CONSTRAINT ck_outbox_events__processed_shape CHECK((status='processed' AND processed_at IS NOT NULL) OR (status<>'processed' AND processed_at IS NULL))
);
CREATE INDEX ix_outbox_events__claim ON outbox_events(available_at,created_at,outbox_event_id) WHERE status='pending';
CREATE INDEX ix_outbox_events__expired_lease ON outbox_events(locked_at,outbox_event_id) WHERE status='processing';
CREATE INDEX ix_outbox_events__run ON outbox_events(account_id,conversation_id,run_id,created_at);

CREATE OR REPLACE FUNCTION enforce_run_message_invariants() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_run uuid; r runs%ROWTYPE; msg messages%ROWTYPE; assistant_count integer;
BEGIN
  IF TG_TABLE_NAME = 'runs' THEN
    target_run := COALESCE(NEW.run_id, OLD.run_id);
  ELSE
    target_run := COALESCE(NEW.produced_by_run_id, OLD.produced_by_run_id);
  END IF;
  IF target_run IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO r FROM runs WHERE run_id=target_run;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO msg FROM messages WHERE message_id=r.trigger_message_id;
  IF NOT FOUND OR msg.role<>'user' OR msg.status<>'accepted' THEN RAISE EXCEPTION 'run trigger must be an accepted user message'; END IF;
  IF r.retry_of_run_id IS NULL AND r.context_message_seq<>msg.sequence THEN RAISE EXCEPTION 'initial run context watermark must equal trigger sequence'; END IF;
  IF r.retry_of_run_id IS NOT NULL THEN
    PERFORM 1 FROM runs p WHERE p.run_id=r.retry_of_run_id AND p.status IN ('failed','cancelled') AND p.trigger_message_id=r.trigger_message_id AND p.context_message_seq=r.context_message_seq;
    IF NOT FOUND THEN RAISE EXCEPTION 'invalid retry source'; END IF;
  END IF;
  SELECT count(*) INTO assistant_count FROM messages WHERE produced_by_run_id=r.run_id AND role='assistant';
  IF assistant_count<>1 THEN RAISE EXCEPTION 'each run must have exactly one assistant message'; END IF;
  SELECT * INTO msg FROM messages WHERE produced_by_run_id=r.run_id;
  IF (r.status='completed' AND msg.status<>'completed') OR (r.status='failed' AND msg.status<>'failed') OR (r.status='cancelled' AND msg.status<>'aborted') THEN RAISE EXCEPTION 'run and assistant terminal states disagree'; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ct_runs__message_invariants AFTER INSERT OR UPDATE ON runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION enforce_run_message_invariants();
CREATE CONSTRAINT TRIGGER ct_messages__run_invariants AFTER INSERT OR UPDATE OR DELETE ON messages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION enforce_run_message_invariants();

GRANT USAGE ON SCHEMA hpagent TO hpagent_api, hpagent_worker;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA hpagent TO hpagent_api, hpagent_worker;
ALTER ROLE hpagent_api SET search_path TO hpagent, public;
ALTER ROLE hpagent_worker SET search_path TO hpagent, public;
