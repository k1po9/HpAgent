-- Preserve the account + Run + invocation ownership boundary for nullable Work owners.
SET search_path TO hpagent, public;
ALTER TABLE research_plans ADD CONSTRAINT fk_research_plans__invocation_owner
 FOREIGN KEY(account_id,run_id,invocation_id)
 REFERENCES capability_invocations(account_id,run_id,invocation_id);
ALTER TABLE source_records ADD CONSTRAINT fk_source_records__invocation_owner
 FOREIGN KEY(account_id,run_id,invocation_id)
 REFERENCES capability_invocations(account_id,run_id,invocation_id);
ALTER TABLE research_history_baselines ADD CONSTRAINT fk_research_history__invocation_owner
 FOREIGN KEY(account_id,run_id,invocation_id)
 REFERENCES capability_invocations(account_id,run_id,invocation_id);
