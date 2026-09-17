-- F4.2: route committed approval decisions through the existing Web Outbox.
SET search_path TO hpagent, public;

ALTER TABLE outbox_events DROP CONSTRAINT ck_outbox_events__event_type;
ALTER TABLE outbox_events ADD CONSTRAINT ck_outbox_events__event_type CHECK(
  event_type IN ('start_run','start_research_run','cancel_run','retain_memory',
    'publish_terminal_event','file_action_approval_decided')
);

ALTER TABLE file_action_approvals DROP CONSTRAINT ck_file_action_approvals__status;
ALTER TABLE file_action_approvals ADD CONSTRAINT ck_file_action_approvals__status
  CHECK(status IN ('pending','approved','rejected','expired','cancelled','consumed'));
ALTER TABLE file_action_approvals DROP CONSTRAINT ck_file_action_approvals__decision;
ALTER TABLE file_action_approvals ADD CONSTRAINT ck_file_action_approvals__decision CHECK(
  (status='pending' AND decided_at IS NULL AND decided_by_account_id IS NULL
    AND consumed_at IS NULL)
  OR (status IN ('approved','rejected') AND decided_at IS NOT NULL
    AND decided_by_account_id=account_id AND consumed_at IS NULL)
  OR (status IN ('expired','cancelled') AND consumed_at IS NULL)
  OR (status='consumed' AND decided_at IS NOT NULL
    AND decided_by_account_id=account_id AND consumed_at IS NOT NULL)
);
