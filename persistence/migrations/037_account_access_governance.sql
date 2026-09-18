SET search_path TO hpagent, public;

CREATE TABLE registration_invites (
  invite_id uuid PRIMARY KEY,
  code_hash bytea NOT NULL,
  entitlement_profile jsonb NOT NULL,
  max_redemptions bigint NOT NULL,
  redemption_count bigint NOT NULL DEFAULT 0,
  expires_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_registration_invites__code_hash UNIQUE(code_hash),
  CONSTRAINT ck_registration_invites__profile_object
    CHECK(jsonb_typeof(entitlement_profile) = 'object'),
  CONSTRAINT ck_registration_invites__max_redemptions CHECK(max_redemptions > 0),
  CONSTRAINT ck_registration_invites__redemption_count
    CHECK(redemption_count >= 0 AND redemption_count <= max_redemptions),
  CONSTRAINT ck_registration_invites__timestamps CHECK(updated_at >= created_at)
);
CREATE INDEX ix_registration_invites__available
  ON registration_invites(expires_at, invite_id)
  WHERE revoked_at IS NULL AND redemption_count < max_redemptions;

CREATE TABLE account_entitlements (
  account_id uuid PRIMARY KEY,
  model_access_tier text NOT NULL,
  daily_token_limit bigint,
  prompt_visibility text NOT NULL,
  expires_at timestamptz,
  version bigint NOT NULL DEFAULT 1,
  provisioned_by_invite_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_account_entitlements__accounts
    FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT fk_account_entitlements__registration_invites
    FOREIGN KEY(provisioned_by_invite_id) REFERENCES registration_invites(invite_id)
    ON DELETE RESTRICT,
  CONSTRAINT ck_account_entitlements__tier_not_empty
    CHECK(length(btrim(model_access_tier)) > 0),
  CONSTRAINT ck_account_entitlements__daily_limit
    CHECK(daily_token_limit IS NULL OR daily_token_limit > 0),
  CONSTRAINT ck_account_entitlements__prompt_visibility
    CHECK(prompt_visibility IN ('none','summary','full_safe')),
  CONSTRAINT ck_account_entitlements__version CHECK(version >= 1),
  CONSTRAINT ck_account_entitlements__timestamps CHECK(updated_at >= created_at)
);
CREATE INDEX ix_account_entitlements__invite
  ON account_entitlements(provisioned_by_invite_id)
  WHERE provisioned_by_invite_id IS NOT NULL;
CREATE INDEX ix_account_entitlements__expires
  ON account_entitlements(expires_at) WHERE expires_at IS NOT NULL;

-- Existing accounts predate access governance and retain unrestricted owner access.
INSERT INTO account_entitlements(
  account_id, model_access_tier, daily_token_limit, prompt_visibility
)
SELECT account_id, 'owner', NULL, 'full_safe' FROM accounts
ON CONFLICT(account_id) DO NOTHING;

GRANT SELECT, INSERT ON account_entitlements TO hpagent_api;
GRANT SELECT, UPDATE ON registration_invites TO hpagent_api;
GRANT SELECT ON account_entitlements TO hpagent_worker;
