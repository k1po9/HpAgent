"""QQ presentation and delivery of immutable PostgreSQL committed results."""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from common.types import ChannelType, UnifiedMessage
from persistence.uow import UnitOfWork

logger = logging.getLogger(__name__)


class QQDeliveryAdapter:
    """QQ-only formatting; never uses SSE projection or Agent execution APIs."""

    def __init__(self, router):
        self.router = router

    def parts(self, row):
        payload = row["payload"]
        origin = payload["origin"]
        text = payload["content"] or ""
        if payload.get("files"):
            # Protected file references: no public token or local filesystem disclosure.
            text += "\n附件（需登录原账户下载）：\n" + "\n".join(
                f"/api/v1/files/{fid}/content" for fid in payload["files"]
            )
        parts = [text[i : i + 1500] for i in range(0, len(text), 1500)] or ["（回答为空）"]
        if origin["channel_type"] == "napcat":

            def escape(value):
                return (
                    str(value)
                    .replace("&", "&amp;")
                    .replace("[", "&#91;")
                    .replace("]", "&#93;")
                    .replace(",", "&#44;")
                )

            # CQ control is adapter-owned, never interpreted from model output.
            parts = [escape(part) for part in parts]
            parts[0] = (
                f"[CQ:reply,id={escape(origin['external_message_id'])}]"
                + (
                    f"[CQ:at,qq={escape(origin['sender_id'])}] "
                    if origin["scope"] == "group"
                    else ""
                )
                + parts[0]
            )
        return parts

    async def send(self, row, part):
        origin = row["payload"]["origin"]
        metadata = {**origin["metadata"], "delivery_id": str(row["run_id"]), "msg_seq": part + 1}
        message = UnifiedMessage(
            account_id=str(row["account_id"]),
            session_id=str(row["payload"]["session_id"]),
            sender_id=origin["sender_id"],
            channel_type=ChannelType(origin["channel_type"]),
            content=self.parts(row)[part],
            metadata=metadata,
        )
        ok = await self.router.send(message)
        return "delivered" if ok else "pending"


class QQDeliveryService:
    def __init__(self, database, adapter):
        self.database, self.adapter = database, adapter

    def claim(self):
        with UnitOfWork(self.database) as uow:
            # A crashed sender may already have delivered; never silently resend it.
            uow.execute(
                "UPDATE qq_deliveries SET state='uncertain',last_error='sender_lost',"
                "updated_at=now() WHERE state='sending' AND lease_until<now()"
            )
            row = uow.execute(
                "SELECT * FROM qq_deliveries WHERE state='pending' "
                "AND available_at<=now() ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            token = uuid4()
            uow.execute(
                "UPDATE qq_deliveries SET state='sending',lease_token=%s,"
                "lease_until=now()+interval '120 seconds',attempts=attempts+1 WHERE run_id=%s",
                (token, row["run_id"]),
            )
            return {**row, "lease_token": token}

    def finish(self, row, state, part, error=None):
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE qq_deliveries SET state=%s,next_part=%s,last_error=%s,"
                "available_at=now()+interval '10 seconds',updated_at=now(),lease_until=NULL "
                "WHERE run_id=%s AND lease_token=%s AND state='sending'",
                (state, part, error, row["run_id"], row["lease_token"]),
            )

    def retry_uncertain(self, account_id, run_id):
        """Explicit operator decision: retry the same chunk, accepting duplicate risk."""
        with UnitOfWork(self.database) as uow:
            return (
                uow.execute(
                    "UPDATE qq_deliveries SET state='pending',available_at=now(),"
                    "lease_token=NULL,updated_at=now() WHERE account_id=%s AND run_id=%s "
                    "AND state='uncertain' RETURNING run_id",
                    (account_id, run_id),
                ).fetchone()
                is not None
            )

    async def deliver_once(self):
        row = await asyncio.to_thread(self.claim)
        if row is None:
            return False
        part = row["next_part"]
        try:
            state = await asyncio.wait_for(self.adapter.send(row, part), timeout=60)
        except Exception:
            state = "uncertain"
        if state == "delivered":
            part += 1
            state = "delivered" if part == len(self.adapter.parts(row)) else "pending"
        await asyncio.to_thread(
            self.finish, row, state, part, None if state == "delivered" else state
        )
        return True

    async def run(self):
        while True:
            try:
                await self.deliver_once()
            except Exception:
                logger.exception("QQ delivery consumer failed")
            await asyncio.sleep(1)
