#!/usr/bin/env python3
"""
官方 QQ 机器人连通性测试 —— 不依赖 Temporal / Redis / Hindsight。
只测试: access_token 获取 → WebSocket 连接 → 等待一条推送事件。

用法:
  python scripts/test-official-qq.py

前提: .env 中已填入 QQ_OFFICIAL_APP_ID 和 QQ_OFFICIAL_CLIENT_SECRET
  （可选）QQ_OFFICIAL_SANDBOX=true  使用沙箱环境
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# ── 加载 .env ──
_project_root = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass

APP_ID = os.getenv("QQ_OFFICIAL_APP_ID", "")
CLIENT_SECRET = os.getenv("QQ_OFFICIAL_CLIENT_SECRET", "")
SANDBOX = os.getenv("QQ_OFFICIAL_SANDBOX", "false").lower() in ("true", "1", "yes")

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL = "https://api.sgroup.qq.com/gateway/bot"
SANDBOX_GATEWAY_URL = "https://sandbox.api.sgroup.qq.com/gateway/bot"
API_BASE = "https://sandbox.api.sgroup.qq.com" if SANDBOX else "https://api.sgroup.qq.com"

# Intents（订阅所有常用事件）
INTENTS = (
    (1 << 0)   # GUILDS
    | (1 << 12)  # DIRECT_MESSAGE
    | (1 << 25)  # GROUP_AND_C2C_EVENT
    | (1 << 26)  # INTERACTION
    | (1 << 27)  # MESSAGE_AUDIT
    | (1 << 30)  # PUBLIC_GUILD_MESSAGES
)


async def main():
    import aiohttp
    import websockets

    # ── 校验配置 ──
    if not APP_ID or not CLIENT_SECRET:
        print("[FAIL] QQ_OFFICIAL_APP_ID 或 QQ_OFFICIAL_CLIENT_SECRET 未设置。")
        print("       请在 .env 中填入这两个字段后重试。")
        return

    print(f"AppID: {APP_ID}")
    print(f"Sandbox: {SANDBOX}")
    print(f"API Base: {API_BASE}")
    print()

    # ── 1. 获取 access_token ──
    print("[1/4] 获取 access_token ...")
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, json={
            "appId": APP_ID,
            "clientSecret": CLIENT_SECRET,
        }) as resp:
            if resp.status != 200:
                body = await resp.text()
                print(f"[FAIL] Token 获取失败: HTTP {resp.status}")
                print(f"       {body}")
                return
            data = await resp.json()

        token = data.get("access_token", "")
        if not token:
            print(f"[FAIL] 响应中无 access_token: {data}")
            return
        print(f"[OK] access_token 获取成功 (expires_in={data.get('expires_in')}s)")
        print(f"     token: {token[:12]}...")

        # ── 2. 获取网关地址 ──
        print("\n[2/4] 获取 WebSocket 网关地址 ...")
        gw_url = SANDBOX_GATEWAY_URL if SANDBOX else GATEWAY_URL
        async with session.get(gw_url, headers={"Authorization": f"QQBot {token}"}) as resp:
            data = await resp.json()
        wss_url = data.get("url", "")
        if not wss_url:
            print(f"[FAIL] 网关响应中无 url: {data}")
            return
        print(f"[OK] WSS URL: {wss_url}")
        print(f"     建议分片数: {data.get('shards', 1)}")

    # ── 3. WebSocket 连接 + 握手 ──
    print("\n[3/4] 连接 WebSocket 网关 ...")
    try:
        ws = await websockets.connect(wss_url, ping_interval=20, ping_timeout=10)
    except Exception as e:
        print(f"[FAIL] WebSocket 连接失败: {e}")
        return
    print("[OK] WebSocket 已连接")

    # 等待 Hello
    raw = await asyncio.wait_for(ws.recv(), timeout=15)
    hello = json.loads(raw)
    if hello.get("op") != 10:
        print(f"[FAIL] 期望 OpCode 10 Hello，收到: {hello}")
        await ws.close()
        return
    heartbeat_ms = hello.get("d", {}).get("heartbeat_interval", 41250)
    print(f"[OK] 收到 Hello (heartbeat_interval={heartbeat_ms}ms)")

    # 发送 Identify
    await ws.send(json.dumps({
        "op": 2,
        "d": {
            "token": f"QQBot {token}",
            "intents": INTENTS,
            "shard": [0, 1],
        },
    }))
    print("[OK] 已发送 Identify")

    # 等待 Ready
    raw = await asyncio.wait_for(ws.recv(), timeout=15)
    ready = json.loads(raw)
    if ready.get("t") == "READY":
        session_id = ready.get("d", {}).get("session_id", "")
        print(f"[OK] 收到 READY (session_id={session_id[:12]}...)")
        print(f"[OK] 连接建立成功！机器人已在线。")
    else:
        print(f"[WARN] 期望 READY 事件，收到: {ready.get('t', ready)}")

    # ── 4. 等待一条推送事件（最多等 60s）──
    print(f"\n[4/4] 等待消息事件（60s 超时）...")
    print("       请在 QQ 上向机器人发送一条消息（@机器人 或 私聊）")
    seq = ready.get("s")
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            data = json.loads(raw)
            op = data.get("op")
            if op == 0:
                t = data.get("t", "")
                d = data.get("d", {})
                if t in ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE",
                         "AT_MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE"):
                    author = d.get("author", {})
                    print(f"\n[OK] 收到消息事件: {t}")
                    print(f"     发送者: {author.get('id', '?')}")
                    print(f"     内容: {d.get('content', '')[:100]}")
                    print(f"     完整事件:\n{json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
                    break
                elif t == "11":  # Heartbeat ACK 通过 op 识别
                    pass
                elif t:
                    print(f"[DEBUG] 收到事件: {t}")
                seq = data.get("s", seq)
            elif op == 11:
                pass  # heartbeat ACK
            else:
                print(f"[DEBUG] OpCode {op}")
    except asyncio.TimeoutError:
        print("\n[INFO] 60s 内未收到消息事件。")
        print("       请确认：")
        print("       1. 是否用正确的 QQ 号向机器人发送了消息")
        print("       2. 沙箱模式下是否在沙箱频道/用户列表中")
        print("       3. 生产模式下机器人是否已通过审核")
        print("       WebSocket 连接本身是正常的，只是没有收到消息事件。")

    await ws.close()
    print("\n[NFO] 测试结束。WebSocket 已断开。")


if __name__ == "__main__":
    asyncio.run(main())
