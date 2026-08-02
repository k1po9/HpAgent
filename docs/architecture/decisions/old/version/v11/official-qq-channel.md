# 官方 QQ 机器人渠道 (OfficialQQChannel)

> 版本: v11 | 日期: 2026-06-09

---

## 目录

1. [架构概览](#1-架构概览)
2. [与 NapCat 渠道的对比](#2-与-napcat-渠道的对比)
3. [认证与 Token 管理](#3-认证与-token-管理)
4. [WebSocket 网关连接](#4-websocket-网关连接)
   - [4.1 握手流程](#41-握手流程)
   - [4.2 心跳与保活](#42-心跳与保活)
   - [4.3 断线重连与会话恢复](#43-断线重连与会话恢复)
5. [消息标准化 (normalize_message)](#5-消息标准化-normalize_message)
   - [5.1 事件 → UnifiedMessage 映射表](#51-事件--unifiedmessage-映射表)
   - [5.2 事件过滤](#52-事件过滤)
   - [5.3 消息去重](#53-消息去重)
6. [消息发送 (send_message)](#6-消息发送-send_message)
   - [6.1 API 端点路由](#61-api-端点路由)
   - [6.2 风控与限速](#62-风控与限速)
   - [6.3 Token 过期重试](#63-token-过期重试)
7. [生命周期管理](#7-生命周期管理)
   - [7.1 启动 (start_monitor)](#71-启动-start_monitor)
   - [7.2 停止 (stop_monitor)](#72-停止-stop_monitor)
8. [配置与集成](#8-配置与集成)
   - [8.1 修改的文件](#81-修改的文件)
   - [8.2 ChannelType 枚举](#82-channeltype-枚举)
   - [8.3 Worker 注册](#83-worker-注册)
   - [8.4 渠道身份 (identities.yaml)](#84-渠道身份-identitiesyaml)
9. [消息全链路流程](#9-消息全链路流程)
10. [文件索引](#10-文件索引)

---

## 1. 架构概览

OfficialQQChannel 是 HpAgent 的第四个消息渠道（继 NapCat、Console、Web 之后），通过 **QQ Bot API v2** 协议对接 QQ 官方机器人平台。

与 NapCat 的"本地客户端中转"模式不同，OfficialQQChannel **直接作为 QQ 平台的 WebSocket 客户端**，无需任何本地中间服务：

```
QQ 平台（腾讯服务器）
    │
    ├── WebSocket (wss://api.sgroup.qq.com/websocket/)
    │   HpAgent 作为 WS 客户端主动连接
    │   ├─ OpCode 10 Hello      ← 服务端下发心跳间隔
    │   ├─ OpCode 2  Identify   → 发送 token + intents 鉴权
    │   ├─ OpCode 0  Dispatch   ← READY / 消息事件推送
    │   ├─ OpCode 1  Heartbeat  → 定时心跳（携带最新 seq）
    │   └─ OpCode 11 Heartbeat ACK ← 服务端确认
    │
    └── HTTP REST API
        HpAgent 发送 HTTP POST 回复消息
        ├─ POST /v2/users/{openid}/messages       (单聊)
        ├─ POST /v2/groups/{group_openid}/messages (群聊)
        ├─ POST /channels/{channel_id}/messages    (频道子频道)
        └─ POST /dms/{guild_id}/messages           (频道私信)

┌─────────────────────────────────────────────────────┐
│                  OfficialQQChannel                  │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │  Token 管理   │  │  WS 客户端   │                 │
│  │  7000s 刷新   │  │  心跳 + 重连  │                │
│  └──────┬───────┘  └──────┬───────┘                 │
│         │                 │                          │
│         ▼                 ▼                          │
│  ┌──────────────────────────────────────────────┐    │
│  │           normalize_message()                │    │
│  │   QQ 事件 → UnifiedMessage                  │    │
│  └────────────────────┬─────────────────────────┘    │
│                       │                              │
│                       ▼ callback()                   │
│               Worker.handle_message()                │
│                       │                              │
│                       ▼                              │
│  ┌──────────────────────────────────────────────┐    │
│  │            send_message()                    │    │
│  │   UnifiedMessage → HTTP POST → QQ API       │    │
│  └──────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

**设计原则：**
- **无外部依赖**：不需要 NapCat 那样的本地容器，直接连接 QQ 云服务
- **标准 WS 客户端模式**：遵循 QQ 网关 OpCode 协议，完整实现握手/心跳/重连
- **最小侵入**：完全复用 HpAgent 现有的渠道抽象和责任链（`normalize → callback → agentic loop → send`）

---

## 2. 与 NapCat 渠道的对比

OfficialQQChannel 和 NapCatChannel 都服务于 QQ 消息场景，底层协议和架构完全不同：

| 维度 | NapCatChannel | OfficialQQChannel |
|---|---|---|
| **协议** | OneBot v11 | QQ Bot API v2 |
| **通信角色** | WS **服务端**（等待 NapCat 连接） | WS **客户端**（连接 QQ 网关） |
| **消息发送** | WS 广播 OneBot action（`send_group_msg` 等） | HTTP POST 到 QQ REST API |
| **消息格式** | `post_type: message/notice/request/meta_event` | `t: C2C_MESSAGE_CREATE / GROUP_AT_MESSAGE_CREATE / ...` |
| **用户标识** | QQ 号（全局唯一纯数字） | bot-scoped `openid`（不同 bot 看到的 ID 不同） |
| **认证方式** | 无需认证（正向 WS 免鉴权） | OAuth2 `access_token`（有效期 7200s） |
| **部署依赖** | 需要 `mlikiowa/napcat-docker` 容器 | **无**，内嵌在 HpAgent 进程 |
| **消息模式** | 被动回复 + 主动推送 | **仅被动回复**（主动推送已于 2025.04 停用） |
| **端口** | `8082` 作为 WS 服务端监听 | 不需要暴露端口（出向连接） |
| **群聊** | `detail_type: group`, 使用 `group_id`（数字） | `detail_type: group`, 使用 `group_openid`（字符串） |

> **为什么不做成一个统一渠道？** 两者协议层完全不同，合并会导致复杂的 if/else 分支。独立实现保持代码清晰，通过 `ChannelType` 枚举在编排层统一路由。

---

## 3. 认证与 Token 管理

QQ 官方机器人使用 OAuth2 风格的 `access_token` 进行 API 鉴权。

### 3.1 Token 获取

```
POST https://bots.qq.com/app/getAppAccessToken
Content-Type: application/json

{
    "appId": "<从 .env 读取 QQ_OFFICIAL_APP_ID>",
    "clientSecret": "<从 .env 读取 QQ_OFFICIAL_CLIENT_SECRET>"
}

→ 200 OK
{
    "access_token": "xxxxxxxxxxxxx",
    "expires_in": "7200"
}
```

代码实现：`_fetch_token()` 方法（`src/sandbox/channels/official_qq.py:146`）

- 使用 `asyncio.Lock` 保护并发访问，避免多个协程同时刷新
- 配置缺失时抛出 `RuntimeError`，`start_monitor()` 捕获后返回 `False`

### 3.2 Token 自动刷新

```
Timeline:
  0s     ─── _fetch_token() 获取首次 token
  7000s  ─── 后台 task 自动刷新（提前 200s，留余量）
  14000s ─── 再次刷新
  ...
```

代码实现：`_token_refresh_loop()` 方法，在 `start_monitor()` 中以 `asyncio.create_task` 启动

- 刷新失败不中止循环，下次间隔继续重试
- 所有 HTTP API 请求的 `Authorization` 头格式：`QQBot {access_token}`

### 3.3 沙箱模式

通过 `QQ_OFFICIAL_SANDBOX=true` 环境变量控制：
- **生产环境**：`api.sgroup.qq.com`（仅真实用户/群可触发）
- **沙箱环境**：`sandbox.api.sgroup.qq.com`（仅沙箱配置的频道/用户可触发，无限速）

---

## 4. WebSocket 网关连接

### 4.1 握手流程

```
Client (HpAgent)                        QQ Gateway
     │                                       │
     │  GET /gateway/bot                     │
     │  (获取 WSS URL + 分片建议)              │
     │ ───────────────────────────────────→  │
     │ ←──── WSS URL + shards=1 ──────────  │
     │                                       │
     │  WebSocket connect                    │
     │ ════════════════════════════════════  │
     │ ←──── OpCode 10 Hello ────────────── │
     │       {heartbeat_interval: 41250}      │
     │                                       │
     │  OpCode 2 Identify ────────────────→  │
     │  {token, intents, shard:[0,1]}         │
     │                                       │
     │ ←──── OpCode 0 Dispatch ──────────── │
     │       READY {session_id}               │
     │                                       │
     │  ★ 连接建立，开始接收事件 ★             │
```

代码实现：`_ws_loop()` → `_handle_hello()` （`src/sandbox/channels/official_qq.py:193`）

**Intents 位掩码**：该渠道订阅以下事件类型：

| Intent | 位值 | 对应事件 |
|---|---|---|
| GUILDS | `1<<0` | 频道生命周期（仅订阅，不处理） |
| DIRECT_MESSAGE | `1<<12` | DIRECT_MESSAGE_CREATE |
| GROUP_AND_C2C_EVENT | `1<<25` | C2C_MESSAGE_CREATE, GROUP_AT_MESSAGE_CREATE |
| INTERACTION | `1<<26` | INTERACTION_CREATE |
| MESSAGE_AUDIT | `1<<27` | MESSAGE_AUDIT_PASS/REJECT |
| PUBLIC_GUILD_MESSAGES | `1<<30` | AT_MESSAGE_CREATE |

### 4.2 心跳与保活

```
每 heartbeat_interval 秒（默认 41.25s）:
  Client → OpCode 1 Heartbeat (d = 最新 seq)
  Server → OpCode 11 Heartbeat ACK
```

代码实现：`_heartbeat_loop()` 方法

- `d` 字段填最新收到的 `s` 值（首次为 `null`）
- 心跳 task 在收到 Hello 后创建，在重连/关闭时取消

### 4.3 断线重连与会话恢复

```
连接断开
    │
    ├── 尝试 OpCode 6 Resume
    │   {token, session_id, seq: last_seq}
    │   ├─ 成功 → 收到重放的事件 → 继续循环
    │   └─ 失败 → 重新 Identify
    │
    ├── 指数退避重连
    │   初始 2s → ×1.5 → 最大 60s
    │
    └── OpCode 7 Reconnect (服务端主动要求)
        → 重新连接 + Identify
```

代码实现：`_ws_loop()` 异常处理分支，`_session_id` 和 `_last_seq` 用于 Resume

- `OpCode 9 Invalid Session` → 清除 session_id 和 seq，重新 Identify
- `websockets` 库内置 `ping_interval=20s` 作为连接健康检测

---

## 5. 消息标准化 (normalize_message)

### 5.1 事件 → UnifiedMessage 映射表

| QQ 事件 | detail_type | sender_id 来源 | 关键 metadata 字段 |
|---|---|---|---|
| `C2C_MESSAGE_CREATE` | `private` | `author.id` | `user_openid`, `attachments` |
| `GROUP_AT_MESSAGE_CREATE` | `group` | `author.member_openid` | `group_openid`, `member_openid`, `attachments` |
| `AT_MESSAGE_CREATE` | `guild` | `author.id` | `guild_id`, `channel_id` |
| `DIRECT_MESSAGE_CREATE` | `dm` | `author.id` | `guild_id` |
| `INTERACTION_CREATE` | `interaction` | `user_openid` | `interaction_id` |

每条 `UnifiedMessage` 的 `metadata` 中均保留：
- `detail_type` — 用于回复时的 API 端点选择
- `msg_id` — 原始消息 ID（被动回复必须携带）
- `event_type` — 原始 QQ 事件名（调试用）
- `timestamp` — 原始消息时间戳

### 5.2 事件过滤

以下事件类型 **不产生回调**（仅记录日志或直接忽略）：

```
过滤（返回 None）:
  - MESSAGE_AUDIT_PASS    → 审核通过，仅记录 debug 日志
  - MESSAGE_AUDIT_REJECT  → 审核拒绝，仅记录 debug 日志
  - GUILD_CREATE / UPDATE / DELETE          → 频道管理事件
  - CHANNEL_CREATE / UPDATE / DELETE        → 子频道管理事件
  - GUILD_MEMBER_ADD / UPDATE / REMOVE       → 成员变动
  - MESSAGE_REACTION_ADD / REMOVE            → 表情表态
  - FRIEND_ADD / FRIEND_DEL                  → 好友变动
  - GROUP_ADD_ROBOT / GROUP_DEL_ROBOT        → 机器人进出群
  - FORUM_*                                  → 论坛事件
  - AUDIO_*                                  → 音频事件
```

### 5.3 消息去重

QQ 平台可能在边缘情况下推送重复的 `msg_id`。使用内存缓存去重：

```python
# 处理每条 dispatch 事件时:
if msg_id in self._sent_msg_ids:  # 60s TTL
    return  # 跳过
self._sent_msg_ids[msg_id] = time.time()
```

代码实现：`_handle_dispatch()` → `_clean_dedup_cache()`，TTL 60 秒自动过期清理

---

## 6. 消息发送 (send_message)

### 6.1 API 端点路由

`_build_send_payload()` 方法根据 `detail_type` 选择正确的 QQ API 端点：

```
detail_type = "private"
  → POST {api_base}/v2/users/{user_openid}/messages
  → payload: {content, msg_type: 0, msg_id (被动回复)}

detail_type = "group"
  → POST {api_base}/v2/groups/{group_openid}/messages
  → payload: {content, msg_type: 0, msg_id (被动回复)}

detail_type = "guild"
  → POST {api_base}/channels/{channel_id}/messages
  → payload: {content, msg_type: 0, msg_id (被动回复)}

detail_type = "dm"
  → POST {api_base}/dms/{guild_id}/messages
  → payload: {content, msg_type: 0, msg_id (被动回复)}
```

**当前限制：**
- `msg_type` 固定为 `0`（纯文本）。Markdown/Ark/Embed/Media 等富文本消息类型预留扩展空间
- 群聊消息不携带 `msg_seq`（未启用消息序列号去重）

### 6.2 风控与限速

- **发送间隔**：两次 HTTP POST 之间至少 `0.7s`（`_rate_limit()` 方法）
- **去重窗口**：相同 `(url, content[:50])` 组合在 60s 内只发送一次
- **沙箱环境无限速**（QQ 平台 2026.03 起沙箱环境不限频）

### 6.3 Token 过期重试

```
send_message()
  → HTTP POST
  → 401 Unauthorized
  → _fetch_token()         # 刷新 token
  → HTTP POST (重试一次)
  → 仍失败 → return False
```

---

## 7. 生命周期管理

### 7.1 启动 (start_monitor)

```
start_monitor(callback)
  │
  ├─ 1. 校验配置
  │    os.getenv("QQ_OFFICIAL_APP_ID")     ← .env
  │    os.getenv("QQ_OFFICIAL_CLIENT_SECRET") ← .env
  │    缺失 → return False (日志告警，不影响其他渠道)
  │
  ├─ 2. 创建 aiohttp.ClientSession (HTTP 连接复用)
  │
  ├─ 3. 获取首次 access_token
  │    _fetch_token() → POST bots.qq.com
  │
  ├─ 4. 启动 token 自动刷新 (asyncio task)
  │    _token_refresh_loop() → 每 7000s
  │
  └─ 5. 启动 WebSocket 连接 (asyncio task)
       _ws_loop() → connect → Hello → Identify → 事件循环
```

### 7.2 停止 (stop_monitor)

```
stop_monitor()
  │
  ├─ 1. 设置 _shutdown = True (所有循环感知并退出)
  ├─ 2. 取消 3 个后台 task (heartbeat / token_refresh / reconnect)
  ├─ 3. 关闭 WebSocket 连接
  └─ 4. 关闭 aiohttp.ClientSession
```

---

## 8. 配置与集成

### 8.1 修改的文件

| 文件 | 变更 |
|---|---|
| `src/sandbox/channels/official_qq.py` | **新增** — 渠道核心实现（~460 行） |
| `src/common/types.py` | `ChannelType` 新增 `OFFICIAL_QQ = "official_qq"` |
| `src/sandbox/channels/__init__.py` | 导出 `OfficialQQChannel` |
| `src/orchestration/worker.py` | 注册 + 启动 + 停止 OfficialQQChannel |
| `config/prompts/identities.yaml` | 新增 `official_qq` 身份映射和人设 |
| `config/models.yaml` | 新增 `official_qq` 渠道参数覆盖 |
| `docker-compose.yaml` | 新增 3 个环境变量 |
| `.env` | 新增 3 个配置项（含注释） |

### 8.2 ChannelType 枚举

```python
class ChannelType(str, Enum):
    NAPCAT = "napcat"            # NapCat/OneBot v11 → QQ
    OFFICIAL_QQ = "official_qq"  # 官方 QQ Bot API v2 → QQ (新增)
    WEB = "web"                  # Web 页面 → 浏览器
    CONSOLE = "console"          # 控制台终端 → CLI
```

`HarnessRunner._resolve_channel()` 通过 `ChannelType(raw)` 自动解析，无需额外适配代码。

### 8.3 Worker 注册

在 `start_worker()` 中与 NapCat 并行注册和启动：

```python
# 注册
napcat = NapCatChannel()
deps.channel_router.register(ChannelType.NAPCAT, napcat)

official_qq = OfficialQQChannel()
deps.channel_router.register(ChannelType.OFFICIAL_QQ, official_qq)

# 启动
await napcat.start_monitor(handle_message)
await official_qq.start_monitor(handle_message)

# 停止
await napcat.stop_monitor()
await official_qq.stop_monitor()
```

两个渠道共享同一个 `handle_message` 回调，通过 `AccountService` 区分不同渠道的 `sender_id`。

### 8.4 渠道身份 (identities.yaml)

```yaml
channel_map:
  napcat: napcat
  official_qq: official_qq    # 新增
  console: console
  web: web
  default: default

official_qq: |                 # 新增 — 复用 nono 人设
  nono 是一只正在找主人的暹罗猫...
```

`HarnessContextBuilder._pick_identity()` 通过 `channel_map` 自动选择对应人设注入 system prompt。

---

## 9. 消息全链路流程

以 **群聊 @机器人** 为例，完整链路：

```
Step 1. QQ 网关推送事件
  QQ 平台 → WebSocket → OfficialQQChannel._ws_loop()
  Payload: {op: 0, t: "GROUP_AT_MESSAGE_CREATE", d: {...}, s: 42}

Step 2. OpCode 0 Dispatch 处理
  _handle_dispatch(data)
    → 更新 _last_seq = 42
    → normalize_message(data)
        → event_type = "GROUP_AT_MESSAGE_CREATE"
        → detail_type = "group"
        → sender_id = author.member_openid
        → content = event_data.content
        → metadata = {group_openid, member_openid, msg_id, ...}
        → 返回 UnifiedMessage(channel_type=OFFICIAL_QQ, ...)

Step 3. 去重检查 + 回调
  if msg_id in _sent_msg_ids: skip
  _sent_msg_ids[msg_id] = now
  await self._callback(channel_message)  ← Worker.handle_message()

Step 4. Worker 处理（与 NapCat 相同路径）
  → AccountService.resolve("official_qq", sender_id) → account_id
  → Temporal Workflow (启动或 signal)
  → HarnessRunner.process_turn()
      → _build_context() — 注入 official_qq 人设
      → LLM 推理 → agentic loop
      → _send_response()
          → ChannelRouter.send(unified_message)

Step 5. 渠道发送
  ChannelRouter → OfficialQQChannel.send_message()
      → detail_type = "group"
      → url = POST /v2/groups/{group_openid}/messages
      → payload = {content, msg_type: 0, msg_id (被动回复)}
      → HTTP POST with Authorization: QQBot {token}

Step 6. 用户收到回复
  QQ 平台 → 群聊消息
```

---

## 10. 文件索引

| 文件路径 | 说明 |
|---|---|
| `src/sandbox/channels/official_qq.py` | OfficialQQChannel 完整实现 |
| `src/sandbox/channels/base.py` | 渠道抽象基类 |
| `src/sandbox/channels/router.py` | 渠道路由器 |
| `src/common/types.py` | ChannelType 枚举 + UnifiedMessage 数据结构 |
| `src/orchestration/worker.py` | 渠道注册/启动/停止 + handle_message 回调 |
| `src/harness/runner.py` | HarnessRunner._send_response() → 渠道路由 |
| `src/harness/context_builder.py` | 渠道感知的 system prompt 组装 |
| `config/prompts/identities.yaml` | 渠道人设定义 |
| `config/models.yaml` | 渠道模型参数覆盖 |
| `.env` | QQ 机器人凭据配置 |
| `.claude/skills/qq-offcial-api-docs/` | QQ Bot API v2 完整开发文档（27 篇） |
