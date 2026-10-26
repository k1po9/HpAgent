-- A resource revocation must also cover completed Runs whose Retain event is
-- still queued. Keep this denial on the existing Run, independently of source
-- registration, re-grants, and the current source action (especially deletion).
ALTER TABLE runs ADD COLUMN memory_retention_blocked boolean NOT NULL DEFAULT false;

CREATE OR REPLACE FUNCTION immutable_run_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD.memory_retention_blocked AND NOT NEW.memory_retention_blocked)
    OR ROW(NEW.account_id,NEW.source_kind,NEW.conversation_id,NEW.session_id,NEW.trigger_message_id,
    NEW.context_message_seq,NEW.work_id,NEW.requirement_revision,NEW.work_control_epoch,NEW.wakeup_id,
    NEW.strategy_kind,NEW.executor_key,NEW.executor_version,NEW.strategy_policy_version,NEW.retry_of_run_id)
    IS DISTINCT FROM ROW(OLD.account_id,OLD.source_kind,OLD.conversation_id,OLD.session_id,OLD.trigger_message_id,
    OLD.context_message_seq,OLD.work_id,OLD.requirement_revision,OLD.work_control_epoch,OLD.wakeup_id,
    OLD.strategy_kind,OLD.executor_key,OLD.executor_version,OLD.strategy_policy_version,OLD.retry_of_run_id)
    OR (OLD.status IN ('succeeded','failed','cancelled') AND
        (to_jsonb(NEW)-'memory_retention_blocked') IS DISTINCT FROM
        (to_jsonb(OLD)-'memory_retention_blocked')) THEN
      RAISE EXCEPTION 'immutable Run input/terminal fact';
  END IF;
  RETURN NEW;
END $$;

-- Preserve already recorded denials when upgrading, including a late source
-- that has not been registered yet. New revocations set the flag transactionally
-- and do not rely on timestamps to order registration and revocation.
UPDATE runs r SET memory_retention_blocked=true
WHERE EXISTS (
 SELECT 1 FROM resource_grants g WHERE g.account_id=r.account_id
 AND g.revoked_at IS NOT NULL AND g.revoked_at>=r.created_at
 AND ((g.subject_kind='conversation' AND g.subject_id=r.conversation_id)
   OR (g.subject_kind='work' AND g.subject_id=r.work_id))
) OR EXISTS (
 SELECT 1 FROM memory_sources s JOIN memory_actions a
 ON a.account_id=s.account_id AND a.source_id=s.source_id
 WHERE s.account_id=r.account_id AND s.origin_run_id=r.run_id
 AND a.reason='resource_grant_revoked'
);
-- Keep API/Worker access at the existing command-role boundary.
GRANT UPDATE(memory_retention_blocked) ON runs TO hpagent_api,hpagent_worker;
