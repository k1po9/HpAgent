SET search_path TO hpagent, public;
CREATE TABLE qq_deliveries (
  run_id uuid PRIMARY KEY REFERENCES runs(run_id),
  account_id uuid NOT NULL REFERENCES accounts(account_id),
  payload jsonb NOT NULL,
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','delivered','uncertain')),
  next_part integer NOT NULL DEFAULT 0,
  attempts integer NOT NULL DEFAULT 0,
  lease_token uuid,
  lease_until timestamptz,
  available_at timestamptz NOT NULL DEFAULT now(),
  last_error text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT,INSERT,UPDATE ON qq_deliveries TO hpagent_worker,hpagent_api;
