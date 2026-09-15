SET search_path TO hpagent, public;

-- Source provenance is committed with the accepted Message, never loaded from a
-- live surface cache during execution or memory retention.
ALTER TABLE messages ADD COLUMN origin jsonb NOT NULL DEFAULT '{}'::jsonb
  CHECK (jsonb_typeof(origin)='object');

CREATE TABLE conversation_bindings (
  account_id uuid NOT NULL,
  binding_key text NOT NULL,
  conversation_id uuid NOT NULL,
  route jsonb NOT NULL CHECK (jsonb_typeof(route)='object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,binding_key),
  FOREIGN KEY(account_id,conversation_id)
    REFERENCES conversations(account_id,conversation_id) ON DELETE RESTRICT
);

-- An immutable ingress outcome, including busy/control outcomes. This is a
-- receipt, not an execution queue: it has no pending/claim/dispatch lifecycle.
CREATE TABLE conversation_ingress_receipts (
  account_id uuid NOT NULL,
  message_key text NOT NULL,
  conversation_id uuid NOT NULL,
  request_hash bytea NOT NULL,
  response_status smallint NOT NULL,
  response_body jsonb NOT NULL CHECK (jsonb_typeof(response_body)='object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,message_key),
  FOREIGN KEY(account_id,conversation_id)
    REFERENCES conversations(account_id,conversation_id) ON DELETE RESTRICT
);
GRANT SELECT,INSERT ON conversation_bindings,conversation_ingress_receipts
  TO hpagent_api,hpagent_worker;
GRANT INSERT ON conversations TO hpagent_worker;
GRANT SELECT,INSERT,UPDATE ON idempotency_commands TO hpagent_worker;
