# 跨端记忆共享：现状分析与设计方案

## 1. 问题定义

当前 HpAgent 仅接入 QQ（NapCat）单渠道聊天。后续将接入 Web 端，要求**同一用户账号在 QQ 端和 Web 端的对话记忆共享**——用户在 QQ 上聊过的内容，打开 Web 端后模型应能感知到完整历史。

## 2. 现状分析：为什么不能共享？

### 问题 1：无统一用户账号体系

```python
# src/orchestration/worker.py:91
workflow_id = f"napcat-{message.sender_id}"
```

- QQ 端 `sender_id` = QQ 号（`"12345"`）
- Web 端 `sender_id` = Web 用户 ID（`"user_abc"`）
- **系统无法识别这两个 ID 属于同一自然人**

### 问题 2：session_id 字段被闲置

```python
# src/sandbox/channels/napcat.py:217
return channel_message.to_unified_message(session_id="")
```

`UnifiedMessage.session_id` 在消息流的每一步都是空字符串，从未被赋值。该字段本应是跨渠道会话关联的关键。

### 问题 3：每个渠道创建独立 Workflow，事件完全隔离

```
QQ 端:  workflow_id = "napcat-12345"    → self._events (独立列表)
Web 端: workflow_id = "web-user_abc"    → self._events (独立列表)
```

两个 Workflow 各自维护独立的 `self._events`，互不可见。用户在 QQ 聊 10 轮后打开 Web，模型的历史上下文为零。

### 问题 4：事件无持久化存储

```
事件存储层级：
  OrchestrationWorkflow.self._events[]  ← 唯一存储（Temporal 内存）
  TemporalSessionManager.get_events()    ← Query 读取上述内存（写入 no-op）
  FileSessionRepository                  ← 已实现但未接入主流程
  PostgreSQL                             ← docker-compose 已有但未使用
```

- 事件仅存在于 Workflow 内存中，Workflow 结束后丢失
- `SessionManager` / `FileSessionRepository` 存在于代码中但未被 `init_dependencies()` 初始化或注入
- `docker-compose.yaml` 已部署 PostgreSQL，但 Session 层完全没有使用它

### 问题 5：SessionMetadata 缺少账号维度

```python
# src/common/types.py:154
@dataclass
class SessionMetadata:
    session_id: str          # 会话 ID
    creator_id: str = ""     # 实际上是渠道 sender_id，不是统一账号
    channel_type: ChannelType
    tags: List[str]
    # 缺失: account_id       # 统一用户账号 ID
    # 缺失: channel_bindings # 多渠道关联
```

### 问题 6：Channel 为单例注入，无路由能力

```python
# src/harness/activities.py:17
_channel = None  # 只有 NapCat，无法按消息来源路由到正确通道
```

`send_response_activity` 使用全局唯一的 `_channel` 实例，当有多个渠道时需要路由到正确的通道。

---

## 3. 目标架构

```
                         ┌──────────────────────┐
                         │   AccountService     │  ← 新增
                         │   渠道ID → 统一账号ID   │
                         └──────────┬───────────┘
                                    │ resolve(channel_type, channel_user_id)
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
      ┌───────▼───────┐     ┌──────▼──────┐     ┌───────▼───────┐
      │  NapCatChannel │     │ WebChannel  │     │ ConsoleChannel│
      │  sender_id=QQ号│     │sender_id=   │     │               │
      │                │     │  web_uid    │     │               │
      └───────┬───────┘     └──────┬──────┘     └───────┬───────┘
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
                                   ▼ account_id = "acc-xxx"
                         ┌──────────────────────┐
                         │   SessionManager      │  ← 改造
                         │   find_active(        │
                         │     account_id)       │
                         │   → session_id        │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │ OrchestrationWorkflow │
                         │ workflow_id =         │
                         │   "agent-{account_id}"│  ← 基于账号，非渠道
                         │                      │
                         │ self._events:        │
                         │  ├─ QQ消息(napcat)    │
                         │  ├─ Web消息(web)      │
                         │  └─ ...混合历史...     │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │   PostgreSQL          │  ← 接入已有数据库
                         │   sessions 表         │
                         │   events 表           │
                         │   accounts 表         │
                         └──────────────────────┘
```

---

## 4. 改造方案

### Step 1 — 新增 Account 层 (`src/account/`)

```python
# src/account/models.py
from dataclasses import dataclass, field
from typing import Dict
import time

@dataclass
class Account:
    account_id: str                        # 统一账号 ID (UUID)
    bindings: Dict[str, str] = field(      # 渠道类型 → 渠道用户 ID 映射
        default_factory=dict
    )
    # 例如: {"napcat": "12345", "web": "user_abc"}
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
```

```python
# src/account/account_service.py
class AccountService:
    """渠道 ID → 统一账号 ID 的解析服务"""

    def __init__(self, db_repo):
        self._repo = db_repo

    async def resolve(
        self, channel_type: str, channel_user_id: str
    ) -> str:
        """
        根据渠道类型和渠道用户ID查找或创建统一账号。
        返回 account_id。
        """
        account = await self._repo.find_by_binding(
            channel_type, channel_user_id
        )
        if account:
            return account.account_id
        # 新用户：自动创建账号并绑定
        return await self._repo.create_account(
            channel_type, channel_user_id
        )

    async def bind_channel(
        self, account_id: str, channel_type: str, channel_user_id: str
    ) -> None:
        """为已有账号绑定新渠道（如 Web 登录后绑定 QQ）"""
        await self._repo.add_binding(account_id, channel_type, channel_user_id)
```

### Step 2 — 改造消息入口 (Worker)

```python
# src/orchestration/worker.py — 改造后的 handle_message

async def handle_message(message: UnifiedMessage) -> None:
    if not message.content or not message.content.strip():
        return

    # 1. 渠道 ID → 统一账号 ID
    account_id = await account_service.resolve(
        channel_type=message.channel_type.value,
        channel_user_id=message.sender_id,
    )

    # 2. 查找或创建该账号的活跃 Session
    session = await session_manager.find_active_session(account_id)
    if not session:
        session_id = await session_manager.create_session(
            account_id=account_id,
            channel_type=message.channel_type,
        )
    else:
        session_id = session.session_id

    # 3. workflow_id 基于统一账号，QQ 和 Web 共用
    workflow_id = f"agent-{account_id}"

    # 4. 消息附带完整上下文
    user_message = {
        "content": message.content,
        "sender_id": message.sender_id,
        "channel_type": message.channel_type.value,
        "session_id": session_id,
        "account_id": account_id,
        "metadata": message.metadata,
        "timestamp": message.timestamp,
    }

    # 5. 启动或 signal 已有 Workflow
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(user_message)
    except Exception:
        await client.start_workflow(
            OrchestrationWorkflow.run,
            user_message,
            id=workflow_id,
            task_queue="hpagent-task-queue",
        )
```

### Step 3 — Session 层接入 PostgreSQL

`docker-compose.yaml` 已有 PostgreSQL，直接使用：

```python
# src/session/repositories.py — 新增 PostgresSessionRepository

class PostgresSessionRepository:
    def __init__(self, db_pool):
        self._pool = db_pool

    async def find_active_by_account(
        self, account_id: str
    ) -> Optional[Session]:
        """查找某账号当前活跃的会话"""
        ...

    async def create_session(
        self, account_id: str, channel_type: str
    ) -> Session:
        """创建新会话"""
        ...

    async def append_event(
        self, session_id: str, event: EventRecord
    ) -> int:
        """追加事件到持久化存储"""
        ...

    async def get_events(
        self, session_id: str, offset: int = 0,
        limit: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[EventRecord]:
        """查询会话事件"""
        ...
```

数据库表结构（最小化）：

```sql
CREATE TABLE accounts (
    account_id  TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE account_bindings (
    account_id       TEXT REFERENCES accounts(account_id),
    channel_type     TEXT NOT NULL,        -- 'napcat', 'web'
    channel_user_id  TEXT NOT NULL,        -- QQ号 或 Web用户ID
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (channel_type, channel_user_id)
);

CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,
    account_id   TEXT REFERENCES accounts(account_id),
    status       TEXT DEFAULT 'active',    -- active, archived, completed
    channel_type TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE events (
    event_id     TEXT PRIMARY KEY,
    session_id   TEXT REFERENCES sessions(session_id),
    event_index  INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    content      JSONB DEFAULT '{}',
    metadata     JSONB DEFAULT '{}',
    timestamp    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, event_index)
);

CREATE INDEX idx_events_session ON events(session_id, event_index);
CREATE INDEX idx_sessions_account ON sessions(account_id, status);
```

### Step 4 — ContextBuilder 感知多渠道事件混排

ContextBuilder 基本无需改动——它已能从 `Event.content["channel_type"]` 检测渠道，并在 `_extract_user_content` 中拼接 metadata。

可选的增强：在 system prompt 中追加"跨渠道提示"：

```python
# context_builder.py — _build_system_prompt 末尾追加
def _build_cross_channel_hint(self, events: List[Event]) -> str:
    """检测是否存在跨渠道对话，若有则追加提示。"""
    channels = set()
    for e in events:
        if e.event_type == EventType.USER_MESSAGE:
            ch = e.content.get("channel_type", "")
            if ch:
                channels.add(ch)
    if len(channels) > 1:
        return (
            "注意：用户正在通过多个客户端（{}）与你对话。"
            "对话历史可能来自不同渠道，请无缝衔接上下文。"
        ).format(", ".join(channels))
    return ""
```

### Step 5 — Channel 路由器：响应路由回正确通道

```python
# src/sandbox/channels/router.py — 新增
from common.types import ChannelType, UnifiedMessage
from common.interfaces import IChannel

class ChannelRouter:
    def __init__(self):
        self._channels: Dict[ChannelType, IChannel] = {}

    def register(self, channel_type: ChannelType, channel: IChannel) -> None:
        self._channels[channel_type] = channel

    async def send(self, message: UnifiedMessage) -> bool:
        channel = self._channels.get(message.channel_type)
        if not channel:
            return False
        return await channel.send_message(message)
```

```python
# src/orchestration/worker.py — 注入 ChannelRouter 替代单个 channel
router = ChannelRouter()
router.register(ChannelType.NAPCAT, napcat_channel)
router.register(ChannelType.WEB, web_channel)  # 未来

inject(
    context_builder=ctx_builder,
    resource_pool=pool,
    sandbox_manager=sandbox_mgr,
    channel_router=router,  # 替代原来的 channel
)
```

```python
# src/harness/activities.py — send_response_activity 改用 router
@activity.defn
async def send_response_activity(content, user_message) -> bool:
    if _channel_router is None:
        return False
    msg = UnifiedMessage(...)
    return await _channel_router.send(msg)
```

### Step 6 — Workflow 支持 account_id

```python
# src/orchestration/workflow.py — 小改动
@workflow.run
async def run(self, user_message: Dict[str, Any]) -> Dict[str, Any]:
    self._account_id = user_message.get("account_id", "")
    self._session_id = user_message.get("session_id", "")
    # 其余逻辑不变...
```

---

## 5. 改造后的完整数据流

```
QQ消息
  │
  ▼
NapCatChannel.normalize_message()
  │  UnifiedMessage(sender_id="12345", channel_type=NAPCAT)
  ▼
AccountService.resolve("napcat", "12345")
  │  查 account_bindings 表
  │  → account_id = "acc-xxx" (若无则新建)
  ▼
SessionManager.find_active_session("acc-xxx")
  │  查 sessions 表 WHERE account_id='acc-xxx' AND status='active'
  │  → session_id = "sess-yyy" (若无则新建)
  ▼
workflow_id = "agent-acc-xxx"
  │
  ▼
┌─────────────────────────────────────────────┐
│  OrchestrationWorkflow                      │
│  workflow_id = "agent-acc-xxx"              │
│                                             │
│  self._events:                              │
│  ┌──────────────────────────────────────┐   │
│  │ {"type":"USER_MESSAGE",              │   │
│  │  "channel_type":"napcat",            │   │
│  │  "content":"QQ上问的问题"}            │   │
│  │ {"type":"MODEL_MESSAGE", ...}        │   │
│  │ {"type":"USER_MESSAGE",              │   │
│  │  "channel_type":"web",               │   │
│  │  "content":"Web上继续追问"}           │   │
│  │ ...                                  │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  ContextBuilder 看到完整历史                  │
│  模型感知: "用户先通过QQ问了X,                 │
│             现在通过Web追问Y"                  │
└─────────────────────────────────────────────┘
  │
  ▼
send_response_activity()
  │  UnifiedMessage(channel_type=当前消息来源)
  ▼
ChannelRouter.send(msg)
  │  根据 msg.channel_type 路由
  ├── NAPCAT → NapCatChannel → QQ回复
  └── WEB    → WebChannel   → WebSocket推送
```

---

## 6. 改造量评估

| 改动项 | 文件 | 程度 |
|-------|------|------|
| 新增 Account 模块 | 新建 `src/account/models.py`, `account_service.py` | ~80行 |
| 新增 PG Repository | `src/session/repositories.py` 新增 `PostgresSessionRepository` | ~100行 |
| 新增 ChannelRouter | `src/sandbox/channels/router.py` | ~40行 |
| 改造 Worker 消息入口 | `src/orchestration/worker.py` | ~30行改动 |
| 改造 send_response_activity | `src/harness/activities.py` | ~10行改动 |
| Workflow 支持 account_id | `src/orchestration/workflow.py` | ~5行改动 |
| 初始化依赖注入 | `src/orchestration/worker.py` init_dependencies | ~15行改动 |
| 数据库迁移 SQL | 新建 `migrations/001_accounts.sql` | ~40行 |
| ContextBuilder 跨渠道提示 | `src/harness/context_builder.py` | ~15行新增 |

**总计约 300-350 行新增/改动**，核心是在现有四层架构上增加一个 Account 解析层，并将 Session 从 "per-channel-sender" 提升为 "per-account" 维度。现有 ContextBuilder 和 Workflow 的 agentic loop 逻辑基本不变。

---

## 7. 账号绑定流程

用户首次使用时的账号关联：

```
场景A: 用户先在QQ使用，后登录Web
  1. QQ发消息 → AccountService 自动创建 account + 绑定 napcat:12345
  2. 用户登录Web → Web前端传 web_user_id → 调用 bind_channel API
     → account 新增绑定 web:user_abc
  3. 后续 QQ 和 Web 消息路由到同一 account_id

场景B: 用户在Web注册，后绑定QQ
  1. Web注册 → 创建 account + 绑定 web:user_abc
  2. 用户在Web端输入QQ号验证 → bind_channel(account_id, "napcat", "12345")
  3. 后续 QQ 消息自动关联到该账号

场景C: 纯QQ用户（无Web）
  1. 自动创建 account + 绑定 napcat:12345
  2. 无需额外操作，对用户透明
```

---

## 8. System Prompt 中的跨渠道身份感知

ContextBuilder 根据消息来源选择身份声明，当事件列表包含多个渠道时，追加跨渠道提示。当前已支持的渠道身份：

| 渠道 | 身份声明文件 | 行号 |
|------|-----------|------|
| NapCat (QQ) | `NAPCAT_AGENT_IDENTITY` | `context_builder.py:54` |
| Console | `CONSOLE_AGENT_IDENTITY` | `context_builder.py:68` |
| Web | `WEB_AGENT_IDENTITY` | `context_builder.py:79` |

Web 渠道身份声明已预定义（`context_builder.py:79-85`），只需接入 Web Channel 即可自动生效。
