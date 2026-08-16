SET search_path TO hpagent, public;

CREATE TABLE web_credentials (
  web_credential_id uuid PRIMARY KEY,
  identity_binding_id uuid NOT NULL,
  password_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_web_credentials__identity_binding
    FOREIGN KEY(identity_binding_id) REFERENCES identity_bindings(identity_binding_id)
    ON DELETE RESTRICT,
  CONSTRAINT uq_web_credentials__identity_binding UNIQUE(identity_binding_id),
  CONSTRAINT ck_web_credentials__password_hash CHECK(length(password_hash) BETWEEN 20 AND 2048),
  CONSTRAINT ck_web_credentials__timestamps CHECK(updated_at >= created_at)
);

CREATE TABLE identity_binding_challenges (
  challenge_id uuid PRIMARY KEY,
  account_id uuid NOT NULL,
  provider text NOT NULL,
  challenge_code_hash bytea NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  verified_subject_id text,
  verified_normalized_subject_id text,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  verified_at timestamptz,
  consumed_at timestamptz,
  cancelled_at timestamptz,
  CONSTRAINT fk_identity_binding_challenges__accounts
    FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT ck_identity_binding_challenges__provider CHECK(provider='qq'),
  CONSTRAINT ck_identity_binding_challenges__status
    CHECK(status IN ('pending','completed','cancelled')),
  CONSTRAINT ck_identity_binding_challenges__expiry CHECK(expires_at > created_at),
  CONSTRAINT ck_identity_binding_challenges__state CHECK(
    (status='pending' AND consumed_at IS NULL AND cancelled_at IS NULL)
    OR (status='completed' AND verified_at IS NOT NULL AND consumed_at IS NOT NULL
        AND verified_subject_id IS NOT NULL AND verified_normalized_subject_id IS NOT NULL
        AND cancelled_at IS NULL)
    OR (status='cancelled' AND cancelled_at IS NOT NULL AND consumed_at IS NULL)
  )
);
CREATE UNIQUE INDEX uq_identity_binding_challenges__pending_account_qq
  ON identity_binding_challenges(account_id,provider) WHERE status='pending';
CREATE UNIQUE INDEX uq_identity_binding_challenges__code_hash
  ON identity_binding_challenges(challenge_code_hash);
CREATE INDEX ix_identity_binding_challenges__lookup
  ON identity_binding_challenges(provider,status,expires_at);

-- Consolidation moves an identity and its sessions atomically. Deferral avoids
-- a transient composite-FK violation while both sides are updated.
ALTER TABLE web_auth_sessions
  DROP CONSTRAINT fk_web_auth_sessions__identity_bindings,
  ADD CONSTRAINT fk_web_auth_sessions__identity_bindings
    FOREIGN KEY(account_id,identity_binding_id)
    REFERENCES identity_bindings(account_id,identity_binding_id)
    ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

GRANT SELECT, INSERT, UPDATE ON web_credentials, identity_binding_challenges
  TO hpagent_api;
GRANT INSERT ON accounts TO hpagent_api;
GRANT SELECT, INSERT, UPDATE ON identity_binding_challenges TO hpagent_worker;
GRANT INSERT, UPDATE ON identity_bindings TO hpagent_worker;
GRANT UPDATE ON accounts, web_auth_sessions TO hpagent_worker;
GRANT SELECT(account_id,identity_binding_id) ON web_auth_sessions TO hpagent_worker;
GRANT SELECT ON web_credentials, idempotency_commands TO hpagent_worker;
