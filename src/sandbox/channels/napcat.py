import asyncio
import json
import logging
from typing import Any, Optional, Callable, Awaitable

import websockets

from sandbox.channels.base import BaseChannel, ChannelMessage
from common.types import ChannelType, UnifiedMessage

logger = logging.getLogger("HpAgent")


class NapCatChannel(BaseChannel):
    """
    NapCat协议通道：通过WebSocket接收NapCat客户端的消息，并将其标准化为统一消息格式。
    """
    
    def __init__(self):
        super().__init__(ChannelType.NAPCAT)
        self._host = "0.0.0.0"
        self._port = 8082
        self._server_task: Optional[asyncio.Task] = None
        self._connected_clients = set()

    async def normalize_message(self, raw_message: Any) -> UnifiedMessage:
        """
        将 NapCat/OneBot v11 WebSocket 上报事件标准化为统一消息格式。
        
        支持 post_type: message, notice, request, meta_event。
        根据 OneBot v11 标准，post_type 分为四类：
          - message    消息事件（私聊/群聊/频道）
          - notice     通知事件（群文件上传、管理员变动、成员增减、禁言、好友添加等）
          - request    请求事件（加好友、加群/邀请入群）
          - meta_event 元事件（生命周期、心跳）

        Args:
            raw_message: 原始数据，可以是 JSON 字符串或已解析的 dict

        Returns:
            统一格式的 UnifiedMessage 对象，其中 channel_type 统一为 NAPCAT，
            metadata 中包含 post_type、detail_type、sub_type 等原始上下文。
        """
        if isinstance(raw_message, str):
            try:
                data = json.loads(raw_message)
            except json.JSONDecodeError:
                raise ValueError(f"NapCat protocol error: Invalid JSON message: {raw_message}")
        elif isinstance(raw_message, dict):
            data = raw_message
        else:
            raise ValueError(f"Unsupported message type: {type(raw_message)}")

        post_type = data.get("post_type")
        if not post_type:
            return

        metadata: dict[str, Any] = {
            "post_type": post_type,
            "detail_type": None,
            "sub_type": None,
        }

        sender_id = ""
        content = ""
        channel_type = ChannelType.NAPCAT

        if post_type == "message":
            message_type = data.get("message_type", "")
            metadata["detail_type"] = message_type
            metadata["sub_type"] = data.get("sub_type", "")

            sender_id = str(data.get("sender", {}).get("user_id", ""))
            content = data.get("raw_message", "") or data.get("message", "")

            if message_type == "group":
                group_id = data.get("group_id")
                if group_id is not None:
                    metadata["group_id"] = group_id
            elif message_type == "guild":
                metadata["guild_id"] = data.get("guild_id")
                metadata["channel_id"] = data.get("channel_id")

        elif post_type == "notice":
            notice_type = data.get("notice_type", "")
            metadata["detail_type"] = notice_type
            metadata["sub_type"] = data.get("sub_type", "")

            group_id = data.get("group_id")
            if group_id is not None:
                metadata["group_id"] = group_id

            if notice_type in ("group_upload",):
                sender_id = str(data.get("user_id", ""))
            elif notice_type in ("group_admin",):
                sender_id = str(data.get("user_id", ""))
            elif notice_type in ("group_decrease", "group_increase", "group_ban"):
                sender_id = str(data.get("operator_id", "") or data.get("user_id", ""))
            elif notice_type == "group_recall":
                sender_id = str(data.get("operator_id", ""))
                msg_id = data.get("message_id")
                if msg_id is not None:
                    content = f"msg_id:{msg_id}"
            elif notice_type == "poke":
                sender_id = str(data.get("user_id", ""))
                content = str(data.get("target_id", ""))
            elif notice_type in ("friend_add", "friend_recall", "client_status",
                                 "honor", "lucky_king", "group_card", "offline_file"):
                sender_id = str(data.get("user_id", ""))
            else:
                sender_id = str(data.get("user_id", ""))

        elif post_type == "request":
            request_type = data.get("request_type", "")
            metadata["detail_type"] = request_type
            metadata["sub_type"] = data.get("sub_type", "")

            sender_id = str(data.get("user_id", ""))
            content = data.get("comment", "")

            if request_type == "group":
                group_id = data.get("group_id")
                if group_id is not None:
                    metadata["group_id"] = group_id

            metadata["flag"] = data.get("flag", "")

        elif post_type == "meta_event":
            meta_event_type = data.get("meta_event_type", "")
            metadata["detail_type"] = meta_event_type
            metadata["sub_type"] = data.get("sub_type", "")

            sender_id = str(data.get("self_id", ""))
            content = ""

            if meta_event_type == "heartbeat":
                pass

        else:
            metadata["detail_type"] = post_type
            logger.warning(f"Unknown post_type: {post_type}, treating as generic event")

        channel_message = ChannelMessage(
            sender_id=sender_id,
            content=content,
            channel_type=channel_type,
            metadata=metadata,
        )

        return channel_message.to_unified_message(session_id="")

    async def send_message(self, message: UnifiedMessage) -> bool:
        """向已连接的 NapCat 客户端发送消息。

        Args:
            message: 统一消息对象，metadata 中需包含 detail_type（private/group）
                    以及对应的 user_id (私聊) 或 group_id (群聊)。

        Returns:
            发送是否成功。
        """
        if not self._connected_clients:
            logger.warning("No connected NapCat clients available")
            return False

        # 从标准化元数据中提取目标类型
        detail_type = message.metadata.get("detail_type")
        if detail_type not in ("private", "group"):
            logger.error(f"Invalid or missing detail_type: {detail_type}")
            return False

        # 根据目标类型构造 OneBot API 请求
        if detail_type == "group":
            group_id = message.metadata.get("group_id")
            if not group_id:
                logger.error("Group message target but group_id not found in metadata")
                return False
            payload = {
                "action": "send_group_msg",
                "params": {
                    "group_id": group_id,
                    "message": message.content,
                }
            }
        else:  # private
            user_id = message.sender_id
            if not user_id:
                logger.error("Private message target but sender_id is empty")
                return False
            payload = {
                "action": "send_private_msg",
                "params": {
                    "user_id": user_id,
                    "message": message.content,
                }
            }

        # 记录与发送
        try:
            send_tasks = [
                client.send(json.dumps(payload))
                for client in self._connected_clients
            ]
            # 根据实际风控要求调整延迟，此处保留简单等待
            await asyncio.sleep(2)
            await asyncio.gather(*send_tasks)
            logger.info(f"Sent message to NapCat clients: {message.content}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    async def _handle_message(self, websocket, raw_message: str):
        """处理接收到的消息
        
        1. 标准化消息
        2. 调用回调函数
        3. 对于普通消息，自动回复确认
        
        Args:
            websocket: WebSocket连接，用于发送回复
            raw_message: 原始消息字符串
        """
        try:
            channel_message = await self.normalize_message(raw_message)
            if not channel_message:
                return
            logger.info(f"Received event: {channel_message}")

            if self._callback:
                await self._callback(channel_message)

            if channel_message.metadata.get("post_type") == "message":
                message_type = channel_message.metadata.get("detail_type", "")
                sender_id = channel_message.sender_id
                content = channel_message.content

                logger.info(f"Received {message_type} message from {sender_id}: {content}")

        except json.JSONDecodeError:
            logger.warning(f"Received invalid JSON message: {raw_message}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _main_logic(self, websocket):
        """WebSocket连接的主逻辑
        
        管理客户端连接的生命周期:
        1. 客户端连接时加入连接池
        2. 持续接收并处理消息
        3. 客户端断开时从连接池移除
        
        Args:
            websocket: WebSocket连接对象
        """
        client_addr = websocket.remote_address
        logger.info(f"NapCat client connected: {client_addr}")
        self._connected_clients.add(websocket)

        try:
            async for message in websocket:
                await self._handle_message(websocket, message)
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"NapCat client connection closed unexpectedly: {e.code} - {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error in NapCat connection handler: {e}")
        finally:
            logger.info(f"NapCat client disconnected: {client_addr}")
            self._connected_clients.discard(websocket)

    async def start_monitor(self, callback: Optional[Callable[[UnifiedMessage], Awaitable[None]]] = None) -> bool:
        """启动NapCat消息监控
        
        启动一个WebSocket服务器，监听来自NapCat客户端的连接。
        
        Args:
            callback: 消息回调函数，收到消息时会被调用
            
        Returns:
            是否启动成功
        """
        self._monitoring = True
        self._callback = callback

        async def start_server():
            logger.info(f"HpAgent NapCat channel server starting on ws://{self._host}:{self._port}")
            async with websockets.serve(
                lambda ws, path=None: self._main_logic(ws),
                self._host,
                self._port
            ):
                await asyncio.Future()

        self._server_task = asyncio.create_task(start_server())
        logger.info(f"NapCat channel started monitoring on {self._host}:{self._port}")
        return True

    async def stop_monitor(self) -> bool:
        """停止NapCat消息监控
        
        关闭WebSocket服务器，断开所有客户端连接。
        
        Returns:
            是否停止成功
        """
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