-- Owner-filtered diagnostic queries need exact registered producer identities.
-- No payload access or write privileges are added to the API role.
SET search_path TO hpagent, public;
GRANT SELECT(account_id,run_id,execution_id,operation_id,attempt_no,fencing_token)
 ON execution_operation_attempts TO hpagent_api;
