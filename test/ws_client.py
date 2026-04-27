import asyncio
import json
import websockets

EVENTS = [
    # ---- 消息事件 ----
    {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "sender": {"user_id": 111111},
        "raw_message": "你好机器人"
    },
    {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "group_id": 123456789,
        "sender": {"user_id": 222222},
        "raw_message": "群里的测试消息"
    },

    # ---- 通知事件 ----
    {
        "post_type": "notice",
        "notice_type": "group_increase",
        "sub_type": "approve",
        "group_id": 123456789,
        "operator_id": 333333,
        "user_id": 444444
    },
    {
        "post_type": "notice",
        "notice_type": "poke",
        "user_id": 555555,
        "target_id": 666666
    },

    # ---- 请求事件 ----
    {
        "post_type": "request",
        "request_type": "friend",
        "user_id": 777777,
        "comment": "请加我好友",
        "flag": "abc123"
    },

    # ---- 元事件 ----
    {
        "post_type": "meta_event",
        "meta_event_type": "heartbeat",
        "self_id": 100000,
        "status": {"online": True, "good": True}
    }
]

async def send_and_listen():
    uri = "ws://localhost:8082"
    async with websockets.connect(uri) as ws:
        # 发送测试事件
        for ev in EVENTS:
            payload = json.dumps(ev, ensure_ascii=False)
            print(f"📤 发送: {payload}")
            await ws.send(payload)
            await asyncio.sleep(1)

        print("📡 发送完毕，开始持续监听服务器返回的消息...")
        try:
            async for message in ws:
                print(f"📥 收到: {message}")
        except websockets.exceptions.ConnectionClosed:
            print("🔌 连接已关闭")

asyncio.run(send_and_listen())