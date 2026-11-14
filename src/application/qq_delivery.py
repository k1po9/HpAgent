"""QQ formatting adapter for independent durable deliveries."""

import asyncio
import logging

from application.conversation import context_key_for_route, group_event_id
from common.types import ChannelType, UnifiedMessage
from delivery.service import DeliveryService


class QQDeliveryAdapter:
    def __init__(self, router, group_context=None, *, context_timeout_seconds=.25):
        self.router = router
        self.group_context = group_context
        self.context_timeout_seconds = context_timeout_seconds

    def parts(self, row):
        payload, origin = row["payload"], row["route"]
        text = (
            (payload.get("summary") or payload.get("content"))
            if row["content_scope"] == "summary"
            else payload.get("content")
        )
        text = text or "工作状态已更新，请登录原账户查看。"
        if row["content_scope"] == "content" and payload.get("files"):
            text += "\n附件（需登录原账户下载）：\n" + "\n".join(
                f"/api/v1/files/{fid}/content" for fid in payload["files"]
            )
        parts = [text[i : i + 1500] for i in range(0, len(text), 1500)]
        if origin["channel_type"] == "onebot11":
            segments = [[{"type": "text", "data": {"text": part}}] for part in parts]
            if origin.get("external_message_id"):
                segments[0].insert(0, {"type": "reply", "data": {"id": origin["external_message_id"]}})
            if origin["scope"] == "group":
                segments[0].insert(0, {"type": "at", "data": {"qq": origin["sender_id"]}})
            return segments
        return parts

    async def send(self, row, part):
        origin = row["route"]
        result = await self.router.send_with_receipt(
            UnifiedMessage(
                account_id=str(row["account_id"]),
                session_id=str(row.get("run_id") or row["notification_id"]),
                sender_id=origin["sender_id"],
                channel_type=ChannelType(origin["channel_type"]),
                content="" if origin["channel_type"] == "onebot11" else self.parts(row)[part],
                metadata={
                    **origin["metadata"],
                    "delivery_id": str(row["delivery_id"]),
                    "msg_seq": part + 1,
                    "lease_token": str(row["lease_token"]),
                    **({"onebot_segments": self.parts(row)[part]} if origin["channel_type"] == "onebot11" else {}),
                },
            )
        )
        if result.accepted and self.group_context and origin['scope'] in {'group', 'guild'}:
            try:
                async with asyncio.timeout(self.context_timeout_seconds):
                    parts = self.parts(row)
                    content = (''.join(s['data']['text'] for s in parts[part] if s['type'] == 'text')
                               if origin['channel_type'] == 'onebot11' else parts[part])
                    event_id = (group_event_id(origin, str(result.provider_message_id))
                                if result.provider_message_id is not None
                                else f"delivery:{row['delivery_id']}:{part}")
                    message_type = ('work_notification' if row.get('work_id') else
                                    'assistant_final' if row.get('source_message_id') else 'system_error')
                    await self.group_context.append(
                        origin.get('context_key') or context_key_for_route(origin), event_id=event_id,
                        sender_id=origin['bot_id'], sender_name='bot', content=content,
                        message_type=message_type, thread_id=origin['thread_id'],
                        source_message_id=str(row['source_message_id']) if row.get('source_message_id') else None,
                        delivery_id=str(row['delivery_id']), part_index=part,
                    )
            except asyncio.CancelledError:
                # DeliveryService's overall deadline may cancel us AFTER the
                # provider ack. Returning that known receipt prevents uncertain.
                return result
            except Exception as exc:
                logging.getLogger(__name__).warning('qq_bot_context_degraded reason=%s', type(exc).__name__)
        return result


QQDeliveryService = DeliveryService
