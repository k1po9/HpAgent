SET search_path TO hpagent, public;

-- Phase F: the worker resolves QQ/Web senders to PostgreSQL Accounts through
-- identity_bindings (PostgresAccountService), so it now needs read access to
-- that table.  Identity bindings stay an admin/migration concern: the worker
-- only SELECTs them and must never INSERT/UPDATE/revoke them.
GRANT SELECT ON identity_bindings TO hpagent_worker;
