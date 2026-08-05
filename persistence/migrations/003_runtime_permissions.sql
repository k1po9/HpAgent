SET search_path TO hpagent, public;

REVOKE ALL ON ALL TABLES IN SCHEMA hpagent FROM hpagent_api, hpagent_worker;

GRANT SELECT ON accounts, identity_bindings, web_auth_sessions, conversations,
  messages, sessions, runs, idempotency_commands, outbox_events TO hpagent_api;
GRANT INSERT, UPDATE ON identity_bindings, web_auth_sessions, conversations,
  messages, sessions, runs, idempotency_commands, outbox_events TO hpagent_api;

GRANT SELECT ON accounts, conversations, messages, sessions, runs,
  workflow_executions, outbox_events TO hpagent_worker;
GRANT INSERT, UPDATE ON messages, sessions, runs, workflow_executions,
  outbox_events TO hpagent_worker;

REVOKE ALL ON TABLE schema_migrations FROM hpagent_api, hpagent_worker;
