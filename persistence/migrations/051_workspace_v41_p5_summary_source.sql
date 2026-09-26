SET search_path TO hpagent, public;
CREATE OR REPLACE FUNCTION index_research_file_summary() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
BEGIN
  IF NEW.status='ready' AND NEW.source_run_id IS NOT NULL AND
     NEW.content_type LIKE 'text/markdown%' AND
     (TG_OP='INSERT' OR OLD.status IS DISTINCT FROM 'ready') THEN
    INSERT INTO workspace_file_summaries(account_id,file_id,sha256,summary,source_run_id)
    SELECT NEW.account_id,NEW.file_id,NEW.sha256,left(rr.report_markdown,500),NEW.source_run_id
    FROM research_reports rr JOIN output_publish_operations op
      ON op.account_id=NEW.account_id AND op.file_id=NEW.file_id
      AND op.run_id=NEW.source_run_id
      AND op.operation_id='research:' || NEW.source_run_id::text || ':markdown-output:v1'
    WHERE rr.run_id=NEW.source_run_id
    ON CONFLICT DO NOTHING;
  END IF;
  RETURN NEW;
END $$;
