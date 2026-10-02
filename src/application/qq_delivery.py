"""QQ formatting adapter for independent durable deliveries."""

from common.types import ChannelType, UnifiedMessage
from delivery.service import DeliveryService


class QQDeliveryAdapter:
    def __init__(self, router):
        self.router = router

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
        if origin["channel_type"] == "napcat":

            def escape(value):
                return (
                    str(value)
                    .replace("&", "&amp;")
                    .replace("[", "&#91;")
                    .replace("]", "&#93;")
                    .replace(",", "&#44;")
                )

            parts = [escape(part) for part in parts]
            if origin.get("external_message_id"):
                parts[0] = f"[CQ:reply,id={escape(origin['external_message_id'])}]" + parts[0]
            if origin["scope"] == "group":
                parts[0] = f"[CQ:at,qq={escape(origin['sender_id'])}] " + parts[0]
        return parts

    async def send(self, row, part):
        origin = row["route"]
        return await self.router.send(
            UnifiedMessage(
                account_id=str(row["account_id"]),
                session_id=str(row.get("run_id") or row["notification_id"]),
                sender_id=origin["sender_id"],
                channel_type=ChannelType(origin["channel_type"]),
                content=self.parts(row)[part],
                metadata={
                    **origin["metadata"],
                    "delivery_id": str(row["delivery_id"]),
                    "msg_seq": part + 1,
                },
            )
        )


QQDeliveryService = DeliveryService
