import asyncio
import json
import logging
from typing import Any, Optional, Callable, Awaitable

import websockets

from .base import BaseChannel, ChannelMessage
from ...common.types import ChannelType, UnifiedMessage

logger = logging.getLogger("HpAgent")


class NapCatChannel(BaseChannel):
    def __init__(self):
        super().__init__(ChannelType.NAPCAT)
        self._host = "0.0.0.0"
        self._port = 8090
        self._server_task: Optional[asyncio.Task] = None
        self._connected_clients = set()

    async def normalize_message(self, raw_message: Any) -> UnifiedMessage:
        if isinstance(raw_message, str):
            try:
                data = json.loads(raw_message)
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON message: {raw_message}")
        elif isinstance(raw_message, dict):
            data = raw_message
        else:
            raise ValueError(f"Unsupported message type: {type(raw_message)}")

        message_type = data.get("message_type", "")
        sender_id = str(data.get("sender", {}).get("user_id", ""))
        content = data.get("raw_message", "")
        group_id = data.get("group_id")

        metadata = {
            "post_type": data.get("post_type"),
            "detail_type": message_type,
            "sub_type": data.get("sub_type"),
        }

        if message_type == "group":
            metadata["group_id"] = group_id

        channel_type = ChannelType.GROUP if message_type == "group" else ChannelType.PRIVATE

        channel_message = ChannelMessage(
            sender_id=sender_id,
            content=content,
            channel_type=channel_type,
            metadata=metadata,
        )

        return channel_message.to_unified_message(session_id="")

    async def send_message(self, message: UnifiedMessage) -> bool:
        if not self._connected_clients:
            logger.warning("No connected NapCat clients available")
            return False

        reply_payload = {
            "action": "send_msg",
            "params": {
                "message_type": message.metadata.get("detail_type", "private"),
                "user_id": message.sender_id,
                "message": message.content,
            }
        }

        try:
            send_tasks = [
                client.send(json.dumps(reply_payload))
                for client in self._connected_clients
            ]
            await asyncio.gather(*send_tasks)
            logger.info(f"Sent message to NapCat clients: {message.content}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    async def _handle_message(self, websocket, raw_message: str):
        try:
            channel_message = await self.normalize_message(raw_message)
            logger.info(f"Received event: {channel_message}")

            if self._callback:
                await self._callback(channel_message)

            if channel_message.metadata.get("post_type") == "message":
                message_type = channel_message.metadata.get("detail_type", "")
                sender_id = channel_message.sender_id
                content = channel_message.content

                logger.info(f"Received {message_type} message from {sender_id}: {content}")

                reply_text = f"已收到你的消息: {content}"
                reply_payload = {
                    "action": "send_msg",
                    "params": {
                        "message_type": message_type,
                        "user_id": sender_id,
                        "message": reply_text,
                    }
                }
                await websocket.send(json.dumps(reply_payload))
                logger.info(f"Reply message: {reply_text}")

        except json.JSONDecodeError:
            logger.warning(f"Received invalid JSON message: {raw_message}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _main_logic(self, websocket, path):
        client_addr = websocket.remote_address
        logger.info(f"NapCat client connected: {client_addr}")
        self._connected_clients.add(websocket)

        try:
            async for message in websocket:
                await self._handle_message(websocket, message)
        finally:
            logger.info(f"NapCat client disconnected: {client_addr}")
            self._connected_clients.discard(websocket)

    async def start_monitor(self, callback: Optional[Callable[[UnifiedMessage], Awaitable[None]]] = None) -> bool:
        self._monitoring = True
        self._callback = callback

        async def start_server():
            logger.info(f"WebSocket server starting on ws://{self._host}:{self._port}")
            async with websockets.serve(self._main_logic, self._host, self._port):
                await asyncio.Future()

        self._server_task = asyncio.create_task(start_server())
        logger.info(f"NapCat channel started monitoring on {self._host}:{self._port}")
        return True

    async def stop_monitor(self) -> bool:
        self._monitoring = False
        self._callback = None

        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            self._server_task = None

        self._connected_clients.clear()
        logger.info("NapCat channel stopped monitoring")
        return True