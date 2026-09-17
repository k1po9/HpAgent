-- Research R3/R4: bounded iteration, report claims/citations and Run-owned Artifacts.
SET search_path TO hpagent, public;

ALTER TABLE source_records ADD COLUMN iteration smallint NOT NULL DEFAULT 1;
ALTER TABLE source_records ADD COLUMN question_id varchar(100);
ALTER TABLE source_records ADD CONSTRAINT ck_source_records__iteration
  CHECK(iteration BETWEEN 1 AND 3);
CREATE INDEX ix_source_records__run_iteration
  ON source_records(run_id,iteration,fetch_status,source_id);

CREATE TABLE research_iterations (
  run_id uuid NOT NULL,
  iteration smallint NOT NULL,
  query text NOT NULL,
  status text NOT NULL DEFAULT 'running',
  corroboration jsonb NOT NULL DEFAULT '{}'::jsonb,
  gap_analysis jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id,iteration),
  CONSTRAINT fk_research_iterations__run FOREIGN KEY(run_id)
    REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT ck_research_iterations__iteration CHECK(iteration BETWEEN 1 AND 3),
  CONSTRAINT ck_research_iterations__status
    CHECK(status IN ('running','sufficient','insufficient')),
  CONSTRAINT ck_research_iterations__corroboration
    CHECK(jsonb_typeof(corroboration)='object'),
  CONSTRAINT ck_research_iterations__gap CHECK(jsonb_typeof(gap_analysis)='object')
);

CREATE TABLE research_claims (
  claim_id uuid PRIMARY KEY,
  run_id uuid NOT NULL,
  statement text NOT NULL,
  importance text NOT NULL DEFAULT 'normal',
  ordinal integer NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_research_claims__run FOREIGN KEY(run_id)
    REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT uq_research_claims__ordinal UNIQUE(run_id,ordinal),
  CONSTRAINT ck_research_claims__statement CHECK(length(btrim(statement)) > 0),
  CONSTRAINT ck_research_claims__importance
    CHECK(importance IN ('normal','important')),
  CONSTRAINT ck_research_claims__metadata CHECK(jsonb_typeof(metadata)='object')
);

CREATE TABLE research_citations (
  citation_id uuid PRIMARY KEY,
  run_id uuid NOT NULL,
  claim_id uuid NOT NULL,
  evidence_id uuid NOT NULL,
  locator jsonb NOT NULL,
  verification_status text NOT NULL DEFAULT 'pending',
  verification_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  verified_at timestamptz,
  CONSTRAINT fk_research_citations__run FOREIGN KEY(run_id)
    REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT fk_research_citations__claim FOREIGN KEY(claim_id)
    REFERENCES research_claims(claim_id) ON DELETE CASCADE,
  CONSTRAINT fk_research_citations__evidence FOREIGN KEY(evidence_id)
    REFERENCES evidence_items(evidence_id) ON DELETE RESTRICT,
  CONSTRAINT uq_research_citations__claim_evidence UNIQUE(claim_id,evidence_id),
  CONSTRAINT ck_research_citations__locator CHECK(jsonb_typeof(locator)='object'),
  CONSTRAINT ck_research_citations__status CHECK(
    verification_status IN ('pending','verified','weak_source','invalid')),
  CONSTRAINT ck_research_citations__detail
    CHECK(jsonb_typeof(verification_detail)='object')
);
CREATE INDEX ix_research_citations__run ON research_citations(run_id,claim_id,citation_id);

-- Existing chat Artifacts keep their exact ownership shape. Research reports use
-- a real Run owner instead of synthetic conversations/messages.
ALTER TABLE artifacts ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE artifacts ALTER COLUMN source_message_id DROP NOT NULL;
ALTER TABLE artifacts ADD COLUMN research_run_id uuid;
ALTER TABLE artifacts ADD CONSTRAINT fk_artifacts__research_run
  FOREIGN KEY(research_run_id) REFERENCES runs(run_id) ON DELETE RESTRICT;
ALTER TABLE artifacts ADD CONSTRAINT ck_artifacts__owner_shape CHECK(
  (research_run_id IS NULL AND conversation_id IS NOT NULL AND source_message_id IS NOT NULL)
  OR
  (research_run_id IS NOT NULL AND conversation_id IS NULL AND source_message_id IS NULL)
);
CREATE UNIQUE INDEX uq_artifacts__research_run
  ON artifacts(research_run_id) WHERE research_run_id IS NOT NULL;

CREATE TABLE research_reports (
  run_id uuid PRIMARY KEY,
  artifact_id uuid,
  artifact_version_id uuid,
  report_markdown text NOT NULL,
  report_structured_json jsonb NOT NULL,
  citation_status text NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_research_reports__run FOREIGN KEY(run_id)
    REFERENCES runs(run_id) ON DELETE CASCADE,
  CONSTRAINT fk_research_reports__artifact FOREIGN KEY(artifact_id)
    REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
  CONSTRAINT fk_research_reports__artifact_version FOREIGN KEY(artifact_version_id)
    REFERENCES artifact_versions(artifact_version_id) ON DELETE RESTRICT,
  CONSTRAINT ck_research_reports__markdown CHECK(length(btrim(report_markdown)) > 0),
  CONSTRAINT ck_research_reports__json CHECK(jsonb_typeof(report_structured_json)='object'),
  CONSTRAINT ck_research_reports__citation_status
    CHECK(citation_status IN ('pending','verified','needs_review')),
  CONSTRAINT ck_research_reports__artifact_shape CHECK(
    (artifact_id IS NULL AND artifact_version_id IS NULL)
    OR (artifact_id IS NOT NULL AND artifact_version_id IS NOT NULL))
);

CREATE OR REPLACE FUNCTION publish_research_artifact(
  target_run_id uuid,
  target_artifact_id uuid,
  target_version_id uuid,
  target_html text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO hpagent, public
AS $$
DECLARE owner_account_id uuid; report_title text;
BEGIN
  SELECT r.account_id,t.title INTO owner_account_id,report_title
    FROM runs r JOIN tasks t ON t.task_id=r.task_id
    WHERE r.run_id=target_run_id AND r.run_kind='research';
  IF NOT FOUND THEN RAISE EXCEPTION 'research Run not found'; END IF;
  PERFORM 1 FROM research_reports WHERE run_id=target_run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'research report not found'; END IF;
  INSERT INTO artifacts(artifact_id,account_id,research_run_id,title)
    VALUES(target_artifact_id,owner_account_id,target_run_id,left(report_title,200))
    ON CONFLICT (artifact_id) DO NOTHING;
  INSERT INTO artifact_versions(artifact_version_id,artifact_id,account_id,version,
      status,html,started_at,completed_at)
    VALUES(target_version_id,target_artifact_id,owner_account_id,1,
      'completed',target_html,now(),now())
    ON CONFLICT (artifact_version_id) DO NOTHING;
  UPDATE research_reports SET artifact_id=target_artifact_id,
      artifact_version_id=target_version_id,updated_at=now()
    WHERE run_id=target_run_id;
  RETURN target_artifact_id;
END $$;
REVOKE ALL ON FUNCTION publish_research_artifact(uuid,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION publish_research_artifact(uuid,uuid,uuid,text) TO hpagent_worker;

GRANT SELECT,INSERT,UPDATE ON research_iterations,research_claims,
  research_citations,research_reports TO hpagent_worker;
GRANT SELECT ON research_iterations,research_claims,research_citations,
  research_reports TO hpagent_api;
