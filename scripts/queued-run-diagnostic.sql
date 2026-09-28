-- Usage: psql "$APP_DATABASE_URL" -v run_id='<uuid>' -f scripts/queued-run-diagnostic.sql
-- Read-only view of a queued Run's dispatch path.
SELECT r.run_id, r.status AS run_status,
       o.status AS outbox_status, o.attempt_count, o.locked_by,
       o.last_error_code, o.last_error_message,
       w.workflow_id, w.status AS workflow_execution, w.temporal_run_id,
       CASE
         WHEN o.status = 'pending' AND o.attempt_count = 0 THEN 'dispatcher has not claimed event'
         WHEN o.status = 'pending' THEN 'dispatch retry pending'
         WHEN o.status = 'processing' THEN 'dispatcher processing or lease interrupted'
         WHEN o.status = 'dead_letter' THEN 'dispatch retries exhausted'
         WHEN o.status = 'processed' AND w.status = 'scheduled' THEN 'submitted to Temporal; inspect lifecycle worker'
         ELSE 'inspect run and workflow state'
       END AS diagnosis
FROM hpagent.runs AS r
LEFT JOIN hpagent.outbox_events AS o ON o.run_id = r.run_id AND o.event_type = 'start_run'
LEFT JOIN hpagent.workflow_executions AS w ON w.run_id = r.run_id AND w.is_current
WHERE r.run_id = :'run_id';
