"""Independent delivery; retries never reexecute the producing business Run."""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4, uuid5

from account.identity import normalize_channel_subject
from persistence.uow import UnitOfWork
from work_domain.models import continuation
from work_domain.persistence import WorkRepository


def ensure_inbox(uow, account_id, work_id=None):
    latest = uow.execute(
        "SELECT * FROM delivery_targets WHERE account_id=%s AND work_id IS NOT DISTINCT FROM %s "
        "AND channel='web' ORDER BY target_version DESC LIMIT 1",
        (account_id, work_id),
    ).fetchone()
    if latest and latest["enabled"]:
        return latest["target_id"]
    version = latest["target_version"] + 1 if latest else 1
    target = uuid5(account_id, f"inbox:{work_id or 'chat'}:{version}")
    uow.execute(
        "INSERT INTO delivery_targets(target_id,account_id,work_id,channel,audience,content_scope,target_version) "
        "VALUES (%s,%s,%s,'web','private','content',%s) ON CONFLICT DO NOTHING",
        (target, account_id, work_id, version),
    )
    return target


def target_authorized(uow, target):
    if not target["enabled"]:
        return False
    if not uow.execute(
        "SELECT 1 FROM accounts WHERE account_id=%s AND status='active'", (target["account_id"],)
    ).fetchone():
        return False
    if target["channel"] == "web":
        return True
    return bool(
        uow.execute(
            "SELECT 1 FROM identity_bindings WHERE account_id=%s AND provider=%s "
            "AND normalized_subject_id=%s AND status='active' AND verified_at IS NOT NULL "
            "AND revoked_at IS NULL",
            (target["account_id"], target["identity_provider"], target["identity_subject"]),
        ).fetchone()
    )


def enqueue(
    uow,
    account_id,
    business_key,
    payload,
    *,
    work=None,
    run=None,
    operation_id=None,
    source_event_id=None,
    purpose="fact",
    target_id=None,
    source_message_id=None,
):
    notification = uuid5(account_id, f"notification:{business_key}")
    uow.execute(
        "INSERT INTO notifications(notification_id,account_id,work_id,requirement_revision,control_epoch,"
        "run_id,execution_id,operation_id,source_event_id,purpose,business_key,source_message_id,payload) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT(business_key) DO NOTHING",
        (
            notification,
            account_id,
            work["work_id"] if work else None,
            work["current_requirement_revision"] if work else None,
            work["control_epoch"] if work else None,
            run["run_id"] if run else None,
            run.get("execution_id") if run else None,
            operation_id,
            source_event_id,
            purpose,
            business_key,
            source_message_id,
            json.dumps({"schema_version": 1, **payload}),
        ),
    )
    if target_id is None:
        target_id = ensure_inbox(uow, account_id, work["work_id"] if work else None)
    target = uow.execute(
        "SELECT * FROM delivery_targets WHERE account_id=%s AND target_id=%s",
        (account_id, target_id),
    ).fetchone()
    if (
        not target
        or target["work_id"] != (work["work_id"] if work else None)
        or not target_authorized(uow, target)
    ):
        raise ValueError("notification target unavailable")
    delivery = uuid5(notification, str(target_id))
    uow.execute(
        "INSERT INTO deliveries(delivery_id,account_id,notification_id,target_id,target_version) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(notification_id,target_id) DO NOTHING",
        (delivery, account_id, notification, target_id, target["target_version"]),
    )
    return notification


def cancel_pending(uow, work):
    uow.execute(
        "UPDATE deliveries d SET state='cancelled',updated_at=now() FROM notifications n "
        "WHERE d.notification_id=n.notification_id AND d.account_id=n.account_id AND n.account_id=%s "
        "AND n.work_id=%s AND d.state IN ('pending','failed') AND (n.purpose='fulfillment' "
        "OR n.payload->>'event_type' IN ('completed','result_accepted'))",
        (work["account_id"], work["work_id"]),
    )


def enqueue_event(uow, work, event_id, event_type, run_id=None):
    enqueue(
        uow,
        work["account_id"],
        f"work-event:{event_id}",
        {
            "content": f"{work['title']}: {event_type}",
            "summary": f"Work {work['work_id']}: {event_type}",
            "event_type": event_type,
        },
        work=work,
        source_event_id=event_id,
    )
    if event_type in {"completed", "paused", "stopped", "result_accepted"}:
        for target in uow.execute(
            "SELECT * FROM delivery_targets WHERE account_id=%s AND work_id=%s AND enabled AND channel<>'web'",
            (work["account_id"], work["work_id"]),
        ).fetchall():
            if target_authorized(uow, target):
                enqueue(
                    uow,
                    work["account_id"],
                    f"work-event:{event_id}",
                    {
                        "content": f"{work['title']}: {event_type}",
                        "summary": f"Work {work['work_id']}: {event_type}",
                        "event_type": event_type,
                    },
                    work=work,
                    source_event_id=event_id,
                    target_id=target["target_id"],
                )


def unresolved_delivery(uow, work):
    return uow.execute(
        "SELECT n.operation_id FROM deliveries d JOIN notifications n USING(account_id,notification_id) "
        "WHERE n.account_id=%s AND n.work_id=%s AND n.purpose='fulfillment' "
        "AND d.state IN ('sending','uncertain') ORDER BY n.created_at LIMIT 1",
        (work["account_id"], work["work_id"]),
    ).fetchone()


def reconcile_reminder_receipt(uow, work, run, notification_id, target_id):
    """Resume a one-shot mandate by revalidating a prior channel receipt, without resending."""
    if run["input_snapshot"]["requirement"]["timing"]["kind"] == "daily":
        return None
    prior = uow.execute(
        "SELECT d.* FROM deliveries d JOIN notifications n USING(account_id,notification_id) "
        "JOIN notifications current ON current.account_id=n.account_id AND current.notification_id=%s "
        "WHERE n.account_id=%s AND n.work_id=%s AND n.requirement_revision=%s AND n.purpose='fulfillment' "
        "AND n.run_id<>%s AND n.payload=current.payload AND d.target_id=%s AND d.state='accepted' "
        "AND d.target_version=(SELECT target_version FROM delivery_targets WHERE account_id=%s AND target_id=%s) "
        "ORDER BY d.accepted_at DESC LIMIT 1",
        (
            notification_id,
            work["account_id"],
            work["work_id"],
            work["current_requirement_revision"],
            run["run_id"],
            target_id,
            work["account_id"],
            target_id,
        ),
    ).fetchone()
    if not prior:
        return None
    receipt = {
        **prior["provider_receipt"],
        "revalidated_from_delivery_id": str(prior["delivery_id"]),
    }
    return uow.execute(
        "UPDATE deliveries SET state='accepted',next_part=1,accepted_at=now(),provider_receipt=%s::jsonb,updated_at=now() "
        "WHERE account_id=%s AND notification_id=%s AND target_id=%s AND state='pending' RETURNING *",
        (json.dumps(receipt), work["account_id"], notification_id, target_id),
    ).fetchone()


class DeliveryReceiptHandler:
    @staticmethod
    def apply(uow, notification, delivery):
        if notification["purpose"] != "fulfillment" or delivery["state"] != "accepted":
            return
        work = WorkRepository.get(
            uow, notification["account_id"], notification["work_id"], lock=True
        )
        if (
            work["status"] != "active"
            or work["current_requirement_revision"] != notification["requirement_revision"]
            or work["control_epoch"] != notification["control_epoch"]
            or work["continuation"].get("receipt_ref") != str(notification["notification_id"])
        ):
            return
        run = dict(
            uow.execute(
                "SELECT * FROM runs WHERE account_id=%s AND run_id=%s",
                (notification["account_id"], notification["run_id"]),
            ).fetchone()
        )
        requirement = WorkRepository.requirement(
            uow, work["account_id"], work["work_id"], work["current_requirement_revision"]
        )
        from work_domain.integration import criteria_satisfied, supplemental_evidence

        evidence = supplemental_evidence(uow, work, run, requirement["acceptance_criteria"])
        if not criteria_satisfied(requirement["acceptance_criteria"], evidence):
            next_step = continuation(
                "awaiting_input",
                "user_acceptance_required",
                receipt_ref=str(notification["notification_id"]),
            )
            uow.execute(
                "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=now() WHERE work_id=%s",
                (json.dumps(next_step), work["work_id"]),
            )
            WorkRepository.event(
                uow,
                WorkRepository.get(uow, work["account_id"], work["work_id"]),
                "delivery_accepted",
                run_id=run["run_id"],
            )
            return
        from work_domain.completion import WorkCompletionPolicy

        completed = WorkCompletionPolicy.accept(uow, work, run, delivery_evidence=evidence)
        if not completed:
            from datetime import UTC, datetime

            from work_domain.timing import next_daily

            timing = requirement["timing"]
            next_step = (
                continuation(
                    "at_time",
                    "next_daily_occurrence",
                    due_at=next_daily(timing, datetime.now(UTC)).isoformat(),
                )
                if timing["kind"] == "daily"
                else continuation("ready", "delivery_accepted")
            )
            uow.execute(
                "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=now() WHERE work_id=%s",
                (json.dumps(next_step), work["work_id"]),
            )


class DeliveryService:
    def __init__(self, database, adapter=None):
        self.database, self.adapter = database, adapter

    def claim(self):
        with UnitOfWork(self.database) as uow:
            uow.execute("SELECT pg_advisory_xact_lock(4830020403)")
            uow.execute(
                "UPDATE deliveries SET state='uncertain',last_error='sender_lost',updated_at=now() "
                "WHERE state='sending' AND lease_until<now()"
            )
            held = uow.execute(
                "SELECT count(*) AS n FROM deliveries WHERE state='sending'"
            ).fetchone()["n"]
            if held >= int(os.getenv("DELIVERY_GLOBAL_CAPACITY", "32")):
                return None
            row = uow.execute(
                "SELECT d.delivery_id,n.work_id,n.account_id FROM deliveries d JOIN notifications n "
                "USING(account_id,notification_id) LEFT JOIN capacity_turns t ON t.account_id=d.account_id "
                "AND t.resource='delivery' AND t.lane='background' WHERE d.state='pending' AND d.available_at<=now() "
                "AND (SELECT count(*) FROM deliveries held WHERE held.account_id=d.account_id AND held.state='sending')<%s "
                "ORDER BY COALESCE(t.last_admitted_at,'-infinity'::timestamptz),d.available_at,d.delivery_id LIMIT 1",
                (int(os.getenv("DELIVERY_ACCOUNT_CAPACITY", "4")),),
            ).fetchone()
            if not row:
                return None
            work = (
                WorkRepository.get(uow, row["account_id"], row["work_id"], lock=True)
                if row["work_id"]
                else None
            )
            row = uow.execute(
                "SELECT d.*,n.work_id,n.requirement_revision,n.control_epoch,n.purpose,n.payload,n.run_id,n.source_message_id,"
                "t.channel,t.audience,t.content_scope,t.route,t.enabled,t.identity_provider,t.identity_subject "
                "FROM deliveries d JOIN notifications n USING(account_id,notification_id) "
                "JOIN delivery_targets t USING(account_id,target_id) WHERE d.delivery_id=%s "
                "AND d.state='pending' FOR UPDATE OF d SKIP LOCKED",
                (row["delivery_id"],),
            ).fetchone()
            if not row:
                return None
            if (
                not target_authorized(uow, row)
                or row["purpose"] == "fulfillment"
                and (
                    work["status"] != "active"
                    or work["current_requirement_revision"] != row["requirement_revision"]
                    or work["control_epoch"] != row["control_epoch"]
                    or work["continuation"].get("receipt_ref") != str(row["notification_id"])
                )
            ):
                uow.execute(
                    "UPDATE deliveries SET state='cancelled',last_error='authority_revoked',updated_at=now() WHERE delivery_id=%s",
                    (row["delivery_id"],),
                )
                if (
                    work
                    and work["status"] == "active"
                    and work["continuation"].get("receipt_ref") == str(row["notification_id"])
                ):
                    uow.execute(
                        "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=now() WHERE work_id=%s",
                        (
                            json.dumps(
                                continuation(
                                    "blocked",
                                    "notification_target_unavailable",
                                    receipt_ref=str(row["notification_id"]),
                                )
                            ),
                            work["work_id"],
                        ),
                    )
                    WorkRepository.event(
                        uow,
                        WorkRepository.get(uow, work["account_id"], work["work_id"]),
                        "delivery_blocked",
                    )
                return None
            if row["source_message_id"]:
                message = uow.execute(
                    "SELECT content FROM messages WHERE account_id=%s AND produced_by_run_id=%s AND message_id=%s "
                    "AND role='assistant' AND status='completed'",
                    (row["account_id"], row["run_id"], row["source_message_id"]),
                ).fetchone()
                if not message:
                    uow.execute(
                        "UPDATE deliveries SET state='cancelled',last_error='source_unavailable',updated_at=now() WHERE delivery_id=%s",
                        (row["delivery_id"],),
                    )
                    return None
                row = {
                    **row,
                    "payload": {
                        **row["payload"],
                        "content": message["content"],
                        "summary": message["content"],
                    },
                }
            token = uuid4()
            uow.execute(
                "UPDATE deliveries SET state='sending',lease_token=%s,lease_until=now()+interval '120 seconds',"
                "attempts=attempts+1,updated_at=now() WHERE delivery_id=%s",
                (token, row["delivery_id"]),
            )
            uow.execute(
                "INSERT INTO capacity_turns(resource,lane,account_id,last_admitted_at) VALUES ('delivery','background',%s,now()) "
                "ON CONFLICT(resource,lane,account_id) DO UPDATE SET last_admitted_at=now()",
                (row["account_id"],),
            )
            return {**row, "lease_token": token}

    def finish(self, row, state, part, receipt=None, error=None):
        with UnitOfWork(self.database) as uow:
            if row["work_id"]:
                WorkRepository.get(uow, row["account_id"], row["work_id"], lock=True)
            delivery = uow.execute(
                "UPDATE deliveries SET state=%s,next_part=%s,provider_receipt=COALESCE(%s::jsonb,provider_receipt),last_error=%s,"
                "accepted_at=CASE WHEN %s='accepted' THEN now() ELSE NULL END,lease_until=NULL,"
                "available_at=now()+interval '10 seconds',updated_at=now() "
                "WHERE delivery_id=%s AND lease_token=%s AND state='sending' RETURNING *",
                (
                    state,
                    part,
                    json.dumps(receipt) if receipt else None,
                    error,
                    state,
                    row["delivery_id"],
                    row["lease_token"],
                ),
            ).fetchone()
            if delivery and state == "accepted":
                target = uow.execute(
                    "SELECT * FROM delivery_targets WHERE account_id=%s AND target_id=%s",
                    (row["account_id"], row["target_id"]),
                ).fetchone()
                notification = uow.execute(
                    "SELECT * FROM notifications WHERE notification_id=%s",
                    (row["notification_id"],),
                ).fetchone()
                if target_authorized(uow, target):
                    DeliveryReceiptHandler.apply(uow, notification, delivery)

    def retry_uncertain(self, account_id, delivery_id):
        """Explicit decision accepts duplicate risk; it does not erase prior receipts."""
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT d.delivery_id,n.work_id FROM deliveries d JOIN notifications n USING(account_id,notification_id) "
                "WHERE d.account_id=%s AND d.delivery_id=%s AND d.state IN ('failed','uncertain') FOR UPDATE OF d",
                (account_id, delivery_id),
            ).fetchone()
            if not row:
                return False
            if row["work_id"]:
                raise ValueError("Work delivery decisions require a versioned Work command")
            uow.execute(
                "INSERT INTO delivery_decisions(decision_id,account_id,delivery_id,outcome) "
                "VALUES (%s,%s,%s,'retry_accepting_duplicate_risk')",
                (uuid4(), account_id, delivery_id),
            )
            return bool(
                uow.execute(
                    "UPDATE deliveries SET state='pending',lease_token=NULL,available_at=now(),updated_at=now() "
                    "WHERE account_id=%s AND delivery_id=%s AND state IN ('failed','uncertain') RETURNING delivery_id",
                    (account_id, delivery_id),
                ).fetchone()
            )

    async def deliver_once(self):
        row = await asyncio.to_thread(self.claim)
        if not row:
            return False
        part = row["next_part"]
        if row["channel"] == "web":
            await asyncio.to_thread(
                self.finish, row, "accepted", 1, {"level": "account_inbox_committed"}
            )
            return True
        state, receipt = "uncertain", None
        try:
            accepted = await asyncio.wait_for(self.adapter.send(row, part), timeout=60)
            if accepted:
                part += 1
                receipt = {
                    "level": "adapter_accepted",
                    "part": part,
                    "delivery_id": str(row["delivery_id"]),
                }
                state = "accepted" if part == len(self.adapter.parts(row)) else "pending"
            else:
                state = "failed"
        except Exception:
            pass
        await asyncio.to_thread(
            self.finish,
            row,
            state,
            part,
            receipt,
            None if state in {"accepted", "pending"} else state,
        )
        return True

    async def run(self):
        import logging

        while True:
            try:
                await self.deliver_once()
            except Exception:
                logging.getLogger(__name__).exception("Notification delivery failed")
            await asyncio.sleep(1)


def target_from_origin(
    uow, account_id, work_id, origin, *, content_scope="summary", command_id=None
):
    identity = normalize_channel_subject(origin["channel_type"], origin["sender_id"])
    if not identity:
        raise ValueError("invalid target identity")
    audience = "group" if origin["scope"] in {"group", "guild"} else "private"
    if audience == "group" and content_scope != "summary":
        raise ValueError("group targets accept summaries only")
    target = {
        "account_id": account_id,
        "enabled": True,
        "channel": origin["channel_type"],
        "identity_provider": identity[0],
        "identity_subject": identity[1],
    }
    if not target_authorized(uow, target):
        raise ValueError("target binding unavailable")
    target_id = uuid4()
    uow.execute(
        "INSERT INTO delivery_targets(target_id,account_id,work_id,channel,identity_provider,identity_subject,"
        "route,audience,content_scope,target_version,selected_by_command_id) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,1,%s)",
        (
            target_id,
            account_id,
            work_id,
            origin["channel_type"],
            identity[0],
            identity[1],
            json.dumps(origin),
            audience,
            content_scope,
            command_id,
        ),
    )
    return target_id
