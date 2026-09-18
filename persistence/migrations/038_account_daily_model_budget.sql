SET search_path TO hpagent, public;

-- quota_date is the UTC calendar date chosen by the application at reservation time.
CREATE TABLE account_daily_model_budgets (
  account_id uuid NOT NULL,
  quota_date date NOT NULL,
  used_tokens bigint NOT NULL DEFAULT 0,
  reserved_tokens bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,quota_date),
  CONSTRAINT fk_account_daily_model_budgets__account
    FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE RESTRICT,
  CONSTRAINT ck_account_daily_model_budgets__amounts
    CHECK(used_tokens >= 0 AND reserved_tokens >= 0),
  CONSTRAINT ck_account_daily_model_budgets__timestamps CHECK(updated_at >= created_at)
);

CREATE TABLE account_model_usage_ledger (
  account_id uuid NOT NULL,
  quota_date date NOT NULL,
  operation_id varchar(200) NOT NULL,
  snapshot_id uuid,
  state text NOT NULL,
  reserved_tokens bigint NOT NULL,
  actual_tokens bigint,
  usage_source text,
  created_at timestamptz NOT NULL DEFAULT now(),
  settled_at timestamptz,
  PRIMARY KEY(account_id,quota_date,operation_id),
  CONSTRAINT fk_account_model_usage_ledger__daily
    FOREIGN KEY(account_id,quota_date)
    REFERENCES account_daily_model_budgets(account_id,quota_date) ON DELETE RESTRICT,
  CONSTRAINT ck_account_model_usage_ledger__state
    CHECK(state IN ('reserved','settled','released')),
  CONSTRAINT ck_account_model_usage_ledger__amounts
    CHECK(reserved_tokens >= 0 AND (actual_tokens IS NULL OR actual_tokens >= 0)),
  CONSTRAINT ck_account_model_usage_ledger__source
    CHECK(usage_source IS NULL OR usage_source IN ('provider','measured','estimated')),
  CONSTRAINT ck_account_model_usage_ledger__shape CHECK(
    (state='reserved' AND actual_tokens IS NULL AND usage_source IS NULL AND settled_at IS NULL) OR
    (state='settled' AND actual_tokens IS NOT NULL AND usage_source IS NOT NULL AND settled_at IS NOT NULL) OR
    (state='released' AND actual_tokens IS NULL AND usage_source IS NULL AND settled_at IS NOT NULL)
  )
);

GRANT SELECT,INSERT,UPDATE ON account_daily_model_budgets,account_model_usage_ledger
  TO hpagent_worker;
GRANT SELECT ON account_daily_model_budgets,account_model_usage_ledger TO hpagent_api;
