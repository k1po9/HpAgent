-- FILE-P0-01: tenant-owned immutable files and idempotent Run budgets.
SET search_path TO hpagent, public;

CREATE TABLE stored_files (
  file_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  purpose text NOT NULL,
  status text NOT NULL DEFAULT 'uploading',
  original_name varchar(255) NOT NULL,
  display_name varchar(255) NOT NULL,
  storage_key varchar(512),
  content_type varchar(255),
  encoding varchar(64),
  size_bytes bigint,
  sha256 char(64),
  failure_code varchar(100),
  created_at timestamptz NOT NULL DEFAULT now(),
  ready_at timestamptz,
  expires_at timestamptz,
  deleted_at timestamptz,
  CONSTRAINT uq_stored_files__scope UNIQUE(account_id,conversation_id,file_id),
  CONSTRAINT fk_stored_files__conversation FOREIGN KEY(account_id,conversation_id)
    REFERENCES conversations(account_id,conversation_id) ON DELETE RESTRICT,
  CONSTRAINT ck_stored_files__purpose CHECK(purpose IN ('input','output')),
  CONSTRAINT ck_stored_files__status CHECK(status IN ('uploading','ready','rejected','deleted')),
  CONSTRAINT ck_stored_files__names CHECK(
    length(original_name) BETWEEN 1 AND 255 AND length(display_name) BETWEEN 1 AND 255),
  CONSTRAINT ck_stored_files__storage_key CHECK(
    storage_key IS NULL OR (storage_key !~ '(^/|(^|/)\.\.(/|$)|[\\])' AND length(storage_key) <= 512)),
  CONSTRAINT ck_stored_files__size CHECK(size_bytes IS NULL OR size_bytes >= 0),
  CONSTRAINT ck_stored_files__sha256 CHECK(sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_stored_files__state_shape CHECK(
    (status='uploading' AND ready_at IS NULL AND sha256 IS NULL AND failure_code IS NULL AND deleted_at IS NULL) OR
    (status='ready' AND storage_key IS NOT NULL AND size_bytes IS NOT NULL AND sha256 IS NOT NULL AND ready_at IS NOT NULL AND failure_code IS NULL AND deleted_at IS NULL) OR
    (status='rejected' AND ready_at IS NULL AND failure_code IS NOT NULL AND deleted_at IS NULL) OR
    (status='deleted' AND deleted_at IS NOT NULL)),
  CONSTRAINT ck_stored_files__timestamps CHECK(
    (ready_at IS NULL OR ready_at >= created_at) AND
    (expires_at IS NULL OR expires_at >= created_at) AND
    (deleted_at IS NULL OR deleted_at >= created_at))
);
CREATE INDEX ix_stored_files__account_status ON stored_files(account_id,status,created_at DESC);
CREATE INDEX ix_stored_files__cleanup ON stored_files(expires_at,file_id)
  WHERE status IN ('uploading','rejected','deleted');

CREATE TABLE message_files (
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  message_id uuid NOT NULL,
  file_id uuid NOT NULL,
  role text NOT NULL,
  ordinal smallint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(message_id,file_id),
  CONSTRAINT uq_message_files__ordinal UNIQUE(message_id,role,ordinal),
  CONSTRAINT fk_message_files__message FOREIGN KEY(account_id,conversation_id,message_id)
    REFERENCES messages(account_id,conversation_id,message_id) ON DELETE RESTRICT,
  CONSTRAINT fk_message_files__file FOREIGN KEY(account_id,conversation_id,file_id)
    REFERENCES stored_files(account_id,conversation_id,file_id) ON DELETE RESTRICT,
  CONSTRAINT ck_message_files__role CHECK(role IN ('input','output')),
  CONSTRAINT ck_message_files__ordinal CHECK(ordinal >= 0)
);

CREATE TABLE run_files (
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  run_id uuid NOT NULL,
  file_id uuid NOT NULL,
  direction text NOT NULL,
  logical_name varchar(255) NOT NULL,
  operation_id varchar(200),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id,file_id),
  CONSTRAINT uq_run_files__logical_name UNIQUE(run_id,direction,logical_name),
  CONSTRAINT uq_run_files__operation UNIQUE(run_id,operation_id),
  CONSTRAINT fk_run_files__run FOREIGN KEY(account_id,conversation_id,run_id)
    REFERENCES runs(account_id,conversation_id,run_id) ON DELETE RESTRICT,
  CONSTRAINT fk_run_files__file FOREIGN KEY(account_id,conversation_id,file_id)
    REFERENCES stored_files(account_id,conversation_id,file_id) ON DELETE RESTRICT,
  CONSTRAINT ck_run_files__direction CHECK(direction IN ('input','output')),
  CONSTRAINT ck_run_files__logical_name CHECK(
    length(logical_name) BETWEEN 1 AND 255 AND logical_name !~ '(^/|(^|/)\.\.(/|$)|[\\])'),
  CONSTRAINT ck_run_files__operation CHECK(
    (direction='input' AND operation_id IS NULL) OR
    (direction='output' AND operation_id IS NOT NULL))
);

CREATE TABLE run_budgets (
  run_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  conversation_id uuid NOT NULL,
  policy_version varchar(100) NOT NULL,
  mode text NOT NULL DEFAULT 'observe',
  status text NOT NULL DEFAULT 'ok',
  limits jsonb NOT NULL,
  used jsonb NOT NULL DEFAULT '{}'::jsonb,
  reserved jsonb NOT NULL DEFAULT '{}'::jsonb,
  final_response_reserve_tokens bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_run_budgets__run FOREIGN KEY(account_id,conversation_id,run_id)
    REFERENCES runs(account_id,conversation_id,run_id) ON DELETE RESTRICT,
  CONSTRAINT ck_run_budgets__mode CHECK(mode IN ('off','observe','enforce')),
  CONSTRAINT ck_run_budgets__status CHECK(status IN ('ok','exhausted','closed')),
  CONSTRAINT ck_run_budgets__json CHECK(
    jsonb_typeof(limits)='object' AND jsonb_typeof(used)='object' AND jsonb_typeof(reserved)='object'),
  CONSTRAINT ck_run_budgets__reserve CHECK(final_response_reserve_tokens >= 0)
);

CREATE TABLE run_usage_ledger (
  run_id uuid NOT NULL,
  operation_id varchar(200) NOT NULL,
  dimension text NOT NULL,
  state text NOT NULL,
  reserved_amount bigint NOT NULL DEFAULT 0,
  actual_amount bigint,
  usage_source text,
  created_at timestamptz NOT NULL DEFAULT now(),
  settled_at timestamptz,
  PRIMARY KEY(run_id,operation_id,dimension),
  CONSTRAINT fk_run_usage_ledger__budget FOREIGN KEY(run_id)
    REFERENCES run_budgets(run_id) ON DELETE RESTRICT,
  CONSTRAINT ck_run_usage_ledger__dimension CHECK(dimension IN (
    'model_input_tokens','model_output_tokens','model_total_tokens','model_calls',
    'tool_calls','bytes_scanned','bytes_returned_to_model','bytes_written',
    'output_file_bytes','wall_time_ms')),
  CONSTRAINT ck_run_usage_ledger__state CHECK(state IN ('reserved','settled','released')),
  CONSTRAINT ck_run_usage_ledger__amounts CHECK(
    reserved_amount >= 0 AND (actual_amount IS NULL OR actual_amount >= 0)),
  CONSTRAINT ck_run_usage_ledger__source CHECK(
    usage_source IS NULL OR usage_source IN ('provider','measured','estimated')),
  CONSTRAINT ck_run_usage_ledger__shape CHECK(
    (state='reserved' AND actual_amount IS NULL AND settled_at IS NULL) OR
    (state='settled' AND actual_amount IS NOT NULL AND usage_source IS NOT NULL AND settled_at IS NOT NULL) OR
    (state='released' AND settled_at IS NOT NULL))
);

CREATE OR REPLACE FUNCTION enforce_file_binding_shape() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE m messages%ROWTYPE; f stored_files%ROWTYPE;
BEGIN
  SELECT * INTO m FROM messages WHERE message_id=NEW.message_id;
  SELECT * INTO f FROM stored_files WHERE file_id=NEW.file_id;
  IF f.status <> 'ready' THEN RAISE EXCEPTION 'only ready files may be bound'; END IF;
  IF NEW.role='input' AND (m.role<>'user' OR f.purpose<>'input') THEN
    RAISE EXCEPTION 'input files require a user message';
  END IF;
  IF NEW.role='output' AND (m.role<>'assistant' OR m.status<>'completed' OR f.purpose<>'output') THEN
    RAISE EXCEPTION 'output files require a completed assistant message';
  END IF;
  RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER ct_message_files__shape AFTER INSERT OR UPDATE ON message_files
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION enforce_file_binding_shape();

ALTER TABLE idempotency_commands DROP CONSTRAINT ck_idempotency_commands__operation;
ALTER TABLE idempotency_commands ADD CONSTRAINT ck_idempotency_commands__operation
  CHECK(operation IN ('create_conversation','send_message','cancel_run','retry_run',
    'bind_identity','revoke_identity','create_upload','delete_file'));

GRANT SELECT,INSERT,UPDATE ON stored_files,message_files,run_files,run_budgets,run_usage_ledger
  TO hpagent_api, hpagent_worker;
REVOKE INSERT,UPDATE ON run_usage_ledger FROM hpagent_api;
REVOKE INSERT,UPDATE ON stored_files FROM hpagent_api;
GRANT INSERT,UPDATE ON stored_files TO hpagent_api;

