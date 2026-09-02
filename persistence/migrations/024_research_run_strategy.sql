-- Research fixed workflows are not agent-strategy runs.
SET search_path TO hpagent, public;

ALTER TABLE runs ALTER COLUMN agent_strategy DROP NOT NULL;
ALTER TABLE runs DROP CONSTRAINT ck_runs__agent_strategy;

-- Use schema conversion instead of DML so the validated CHECK can be added in
-- the same transaction even when runs has referencing foreign-key triggers.
ALTER TABLE runs ALTER COLUMN agent_strategy TYPE text USING (
  CASE WHEN run_kind='research' THEN NULL ELSE agent_strategy END
);

ALTER TABLE runs ADD CONSTRAINT ck_runs__agent_strategy CHECK (
  (run_kind='chat' AND agent_strategy IN ('react','plan_and_execute'))
  OR (run_kind='research' AND agent_strategy IS NULL)
  OR (run_kind='file_job' AND
      (agent_strategy IS NULL OR agent_strategy IN ('react','plan_and_execute')))
);
