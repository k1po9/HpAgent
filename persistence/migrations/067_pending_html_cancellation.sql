-- API cancellation may close only the untouched version named by a terminal fixed Chat Run.
SET search_path TO hpagent, public;
CREATE FUNCTION close_pending_html(a uuid, r uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=hpagent,pg_temp AS $$
DECLARE owner runs%ROWTYPE; target uuid;
BEGIN
 SELECT * INTO owner FROM runs WHERE account_id=a AND run_id=r FOR UPDATE;
 IF NOT FOUND OR owner.executor_key<>'artifact_html' OR owner.status NOT IN ('failed','cancelled') THEN
   RAISE EXCEPTION 'pending HTML closure requires terminal fixed Run'; END IF;
 target:=COALESCE(owner.input_snapshot->>'artifact_version_id',owner.input_snapshot->'requirement'->'spec'->>'artifact_version_id')::uuid;
 UPDATE artifact_versions SET status='failed',failure_code=CASE WHEN owner.status='cancelled'
   THEN 'artifact_cancelled' ELSE COALESCE(owner.failure_code,'artifact_execution_failed') END,
   failure_message='构建未完成。',completed_at=GREATEST(now(),created_at),updated_at=now()
 WHERE account_id=a AND artifact_version_id=target AND status='queued' AND producing_run_id IS NULL;
END $$;
REVOKE ALL ON FUNCTION close_pending_html(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION close_pending_html(uuid,uuid) TO hpagent_api,hpagent_worker;
