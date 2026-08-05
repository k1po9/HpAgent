SET search_path TO hpagent, public;
-- Cancel commands must distinguish queued-without-execution from possibly-started.
-- API may read this mapping but cannot insert or mutate it.
GRANT SELECT ON workflow_executions TO hpagent_api;
