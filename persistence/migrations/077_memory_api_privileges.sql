-- Governance API mutates only command intent; workers own remote receipts and source scope.
REVOKE INSERT,UPDATE ON memory_sources FROM hpagent_api;
GRANT UPDATE(generation,state,manual_curation,current_action_id,updated_at) ON memory_sources TO hpagent_api;
REVOKE INSERT ON memory_actions FROM hpagent_api;
GRANT INSERT(action_id,account_id,source_id,generation,kind,actor,reason,action_key,
 request_hash,request_payload,target_memory_id,affected_scopes,status,verified_at,invalidation_epoch)
 ON memory_actions TO hpagent_api;
