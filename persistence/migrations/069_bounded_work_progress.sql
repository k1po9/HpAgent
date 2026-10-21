-- Revision-scoped progress facts and successful daily comparison baselines.
SET search_path TO hpagent, public;
CREATE TABLE work_progress (
 account_id uuid NOT NULL, work_id uuid NOT NULL, requirement_revision bigint NOT NULL,
 policy_version integer NOT NULL DEFAULT 1 CHECK(policy_version=1),
 failure_limit integer NOT NULL CHECK(failure_limit BETWEEN 1 AND 100),
 no_progress_limit integer NOT NULL CHECK(no_progress_limit BETWEEN 1 AND 100),
 consecutive_failures integer NOT NULL DEFAULT 0 CHECK(consecutive_failures>=0),
 no_progress_count integer NOT NULL DEFAULT 0 CHECK(no_progress_count>=0),
 last_failure bytea, blocked_reason text CHECK(blocked_reason IN ('repeated_failure','no_progress')),
 PRIMARY KEY(account_id,work_id,requirement_revision),
 FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_requirements(account_id,work_id,revision)
);
CREATE TABLE work_progress_samples (
 account_id uuid NOT NULL, work_id uuid NOT NULL, requirement_revision bigint NOT NULL,
 invocation_id text NOT NULL, outcome text NOT NULL, failure_signature bytea,
 new_facts integer NOT NULL CHECK(new_facts>=0), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,invocation_id),
 FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_progress,
 FOREIGN KEY(account_id,invocation_id) REFERENCES capability_invocations(account_id,invocation_id)
);
CREATE TABLE work_progress_facts (
 account_id uuid NOT NULL, work_id uuid NOT NULL, requirement_revision bigint NOT NULL,
 fingerprint bytea NOT NULL CHECK(octet_length(fingerprint)=32), invocation_id text NOT NULL,
 PRIMARY KEY(account_id,work_id,requirement_revision,fingerprint),
 FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_progress,
 FOREIGN KEY(account_id,invocation_id) REFERENCES capability_results(account_id,invocation_id)
);
CREATE TABLE work_daily_results (
 account_id uuid NOT NULL, work_id uuid NOT NULL, requirement_revision bigint NOT NULL,
 run_id uuid NOT NULL, previous_run_id uuid, signature bytea NOT NULL CHECK(octet_length(signature)=32),
 changed boolean NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,run_id),
 UNIQUE(account_id,work_id,requirement_revision,run_id),
 FOREIGN KEY(account_id,work_id,requirement_revision) REFERENCES work_requirements(account_id,work_id,revision),
 FOREIGN KEY(account_id,run_id) REFERENCES runs(account_id,run_id),
 FOREIGN KEY(account_id,work_id,requirement_revision,previous_run_id)
 REFERENCES work_daily_results(account_id,work_id,requirement_revision,run_id)
);
CREATE TABLE work_daily_result_refs (
 account_id uuid NOT NULL, run_id uuid NOT NULL, ref_id uuid NOT NULL,
 PRIMARY KEY(account_id,run_id,ref_id),
 FOREIGN KEY(account_id,run_id) REFERENCES work_daily_results,
 FOREIGN KEY(account_id,ref_id) REFERENCES capability_result_refs(account_id,ref_id)
);
GRANT SELECT ON work_progress,work_progress_samples,work_progress_facts,work_daily_results,work_daily_result_refs TO hpagent_api,hpagent_worker;
GRANT INSERT,UPDATE ON work_progress TO hpagent_worker;
-- API commands can reset counts only through the constrained function below.
GRANT INSERT ON work_progress_samples,work_progress_facts,work_daily_results,work_daily_result_refs TO hpagent_worker;
CREATE FUNCTION reset_work_progress(a uuid,w uuid,r bigint) RETURNS void LANGUAGE sql SECURITY DEFINER
 SET search_path=hpagent,pg_temp AS $$
 UPDATE work_progress SET consecutive_failures=0,no_progress_count=0,last_failure=NULL,blocked_reason=NULL
 WHERE account_id=a AND work_id=w AND requirement_revision=r;
$$;
REVOKE ALL ON FUNCTION reset_work_progress(uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reset_work_progress(uuid,uuid,bigint) TO hpagent_api,hpagent_worker;
