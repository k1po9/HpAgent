-- Version-bound Research summary index. Contents require read_content authorization.
SET search_path TO hpagent, public;
CREATE TABLE workspace_file_summaries (
  account_id uuid NOT NULL,
  file_id uuid NOT NULL,
  sha256 char(64) NOT NULL,
  summary text NOT NULL,
  source_run_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,file_id),
  FOREIGN KEY(account_id,file_id) REFERENCES stored_files(account_id,file_id) ON DELETE RESTRICT,
  FOREIGN KEY(account_id,source_run_id) REFERENCES runs(account_id,run_id) ON DELETE RESTRICT,
  CHECK(length(summary) <= 500)
);
CREATE INDEX ix_workspace_file_summaries__text ON workspace_file_summaries
  USING gin(to_tsvector('simple',summary));
CREATE OR REPLACE FUNCTION index_research_file_summary() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
BEGIN
  IF NEW.status='ready' AND NEW.source_run_id IS NOT NULL AND
     (TG_OP='INSERT' OR OLD.status IS DISTINCT FROM 'ready') THEN
    INSERT INTO workspace_file_summaries(account_id,file_id,sha256,summary,source_run_id)
    SELECT NEW.account_id,NEW.file_id,NEW.sha256,left(rr.report_markdown,500),NEW.source_run_id
    FROM research_reports rr WHERE rr.run_id=NEW.source_run_id
    ON CONFLICT DO NOTHING;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_stored_files__research_summary AFTER INSERT OR UPDATE OF status ON stored_files
  FOR EACH ROW EXECUTE FUNCTION index_research_file_summary();
GRANT SELECT ON workspace_file_summaries TO hpagent_api,hpagent_worker;
