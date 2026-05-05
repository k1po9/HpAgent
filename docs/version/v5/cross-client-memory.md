# 跨端记忆共享：v5 实现

## 1. 解决的问题

v4 中 HpAgent 仅接入 QQ（NapCat）单渠道，存在以下问题：

| 问题 | v4 状态 | v5 解决方案 |
|------|---------|------------|
| 无统一账号体系 | `workflow_id = f"napcat-{sender_id}"` — QQ号和Web ID无法关联 | `AccountService.resolve(channel_type, channel_user_id) → account_id` |
| session_id 闲置 | `UnifiedMessage.session_id` 全程为空字符串 | Worker 层调用 SessionManager 创建/查找会话 |
| 渠道事件隔离 | QQ和Web各自独立 Workflow，`self._events` 互不可见 | `workflow_id = f"agent-{account_id}"` — QQ和Web共享同一个 Workflow |
| 事件无持久化 | 仅存在于 Workflow 内存，Workflow 结束后丢失 | FileRepository 已接入 + PostgreSQL 双后端 |
| 响应无法路由 | `_channel` 全局单例，仅 NapCat | `ChannelRouter` 按 `channel_type` 路由到正确通道 |

---

## 2. 核心数据流：渠道消息 → 统一账号 → 共享 Workflow

```
QQ消息                             Web消息 (规划中)
  │                                  │
  ▼                                  ▼
NapCatChannel                      WebChannel
  │ normalize_message()              │ normalize_message()
  ▼                                  ▼
UnifiedMessage(                    UnifiedMessage(
  sender_id="12345",                 sender_id="user_abc",
  channel_type=NAPCAT,               channel_type=WEB,
  account_id="")                     account_id="")
  │                                  │
  ▼                                  ▼
┌─────────────────────────────────────────────────────┐
│              OrchestrationWorker                    │
│                                                     │
│  AccountService.resolve("napcat", "12345")          │
│    → account_id = "acc-xxx"  (首次自动创建)          │
│                                                     │
│  AccountService.resolve("web", "user_abc")          │
│    → account_id = "acc-xxx"  (已绑定，查回同一账号)   │
│                                                     │
│  workflow_id = "agent-acc-xxx"  ← 两个渠道共用      │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│        OrchestrationWorkflow                        │
│        workflow_id = "agent-acc-xxx"                │
│                                                     │
│  self._events:                                      │
│  ┌─────────────────────────────────────────────┐    │
│  │ {type:"USER_MESSAGE", channel_type:"napcat",│    │
│  │  content:"QQ上问的问题", account_id:"acc-xxx"}│    │
│  │ {type:"MODEL_MESSAGE", content:"QQ回复..."} │    │
│  │ {type:"USER_MESSAGE", channel_type:"web",   │    │
│  │  content:"Web上继续追问", account_id:"acc-xxx"}│   │
│  │ {type:"MODEL_MESSAGE", content:"Web回复..."}│    │
│  │ ...                                         │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  ContextBuilder 看到完整历史                          │
│  模型感知: "用户先通过QQ问了X, 现在通过Web追问Y"       │
│                                                     │
│  _build_cross_channel_hint 检测多渠道 →              │
│  追加: "注意：用户正在通过多个客户端（napcat, web）    │
│         与你对话。对话历史可能来自不同渠道，            │
│         请无缝衔接上下文。"                            │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
              send_response_activity()
                       │
                       ▼
              ChannelRouter.send(UnifiedMessage)
                       │
          ┌────────────┴────────────┐
          │ msg.channel_type        │
          ▼                         ▼
    NapCatChannel              WebChannel
    → QQ回复                   → WebSocket推送
```

---

## 3. AccountService 设计 (`src/account/account_service.py`)

```
AccountService
├── _accounts: Dict[str, Account]           # account_id → Account
├── _bindings_index: Dict[str, Dict[str, str]]
│   # channel_type → (channel_user_id → account_id)
│   # 例: {"napcat": {"12345": "acc-xxx"}, "web": {"user_abc": "acc-xxx"}}
│
├── resolve(channel_type, channel_user_id) → account_id
│   # 查 bindings_index，若无则自动创建 Account + 绑定
│
├── bind_channel(account_id, channel_type, channel_user_id)
│   # 为已有账号追加新渠道绑定（如 Web 登录后绑定 QQ 号）
│
├── get_account(account_id) → Account | None
│
└── find_by_binding(channel_type, channel_user_id) → Account | None
```

### 存储后端

| 模式 | 实现 | 适用场景 |
|------|------|---------|
| 内存 | AccountService 内置 `_accounts` + `_bindings_index` dict | 开发/单机 |
| PostgreSQL | `PostgresAccountRepository` (`src/session/repositories.py`) | 生产/持久化 |

---

## 4. ChannelRouter 设计 (`src/sandbox/channels/router.py`)

```
ChannelRouter
├── _channels: Dict[ChannelType, IChannel]
│
├── register(channel_type, channel)    # 注册渠道实现
├── get(channel_type) → IChannel       # 获取渠道实现
└── send(message: UnifiedMessage) → bool
    # 根据 message.channel_type 路由到对应 IChannel.send_message()
```

### 注册方式 (`src/orchestration/worker.py`)

```python
channel_router = ChannelRouter()
channel_router.register(ChannelType.NAPCAT, napcat_channel)
# channel_router.register(ChannelType.WEB, web_channel)    # 未来接入
# channel_router.register(ChannelType.CONSOLE, console_ch)  # 未来接入

inject(channel_router=channel_router)  # 注入到 Harness Activities
```

---

## 5. Workflow 长期运行机制

v5 的 `OrchestrationWorkflow` 从"一次性执行"改为"长期运行 + signal 驱动"：

```
@workflow.run
async def run(self, user_message):
    # 1. 处理初始消息
    self._events.append(USER_MESSAGE)
    await self._process_turn(user_message)

    # 2. 进入等待循环
    while True:
        await workflow.wait_condition(
            lambda: bool(self._pending_messages) or self._completed
        )
        if self._completed:
            break
        if self._pending_messages:
            next_msg = self._pending_messages.pop(0)
            await self._process_turn(next_msg)

@workflow.signal
async def new_message(self, user_message):
    self._events.append(USER_MESSAGE)
    self._pending_messages.append(user_message)
```

关键特性：
- **跨渠道事件累积**：QQ和Web消息都通过 signal 追加到 `self._events`，不区分来源
- **每消息独立 max_turns**：`_process_turn` 内部有 `msg_turns < self._max_turns` 检查
- **优雅退出**：`cancel_session` signal 设置 `_completed=True`，主循环退出

---

## 6. 账号绑定流程

### 场景 A：用户先在 QQ 使用，后登录 Web

```
1. QQ发消息 → AccountService.resolve("napcat", "12345")
   → 查 bindings_index: 无 → 创建 account "acc-xxx"
   → 绑定: {"napcat": "12345"}

2. 用户登录Web → Web前端获得 web_user_id="user_abc"
   → 调用 AccountService.bind_channel("acc-xxx", "web", "user_abc")
   → Account.bindings = {"napcat": "12345", "web": "user_abc"}
   → bindings_index 更新: web:user_abc → acc-xxx

3. 后续 QQ 和 Web 消息 → resolve() → 同一 account_id
   → workflow_id = "agent-acc-xxx" → 共享事件历史
```

### 场景 B：用户在 Web 注册，后绑定 QQ

```
1. Web注册 → resolve("web", "user_abc") → 创建 account + 绑定 web:user_abc

2. 用户在Web端输入QQ号验证
   → bind_channel(account_id, "napcat", "12345")

3. 后续 QQ 消息自动关联到该账号
```

### 场景 C：纯 QQ 用户（无 Web）

```
1. resolve("napcat", "12345") → 自动创建 account + 绑定 napcat:12345
2. 无需额外操作，对用户完全透明
```

---

## 7. 数据库表结构（PostgreSQL）

```sql
-- 统一账号表
CREATE TABLE accounts (
    account_id  TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 渠道绑定表（多对多）
CREATE TABLE account_bindings (
    account_id       TEXT REFERENCES accounts(account_id) ON DELETE CASCADE,
    channel_type     TEXT NOT NULL,        -- 'napcat', 'web'
    channel_user_id  TEXT NOT NULL,        -- QQ号 或 Web用户ID
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (channel_type, channel_user_id)
);

-- 会话表
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,
    account_id   TEXT REFERENCES accounts(account_id) ON DELETE CASCADE,
    status       TEXT DEFAULT 'active',    -- active, archived, completed
    channel_type TEXT,
    creator_id   TEXT DEFAULT '',
    tags         JSONB DEFAULT '[]',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    metadata     JSONB DEFAULT '{}'
);

-- 事件表
CREATE TABLE events (
    event_id     TEXT PRIMARY KEY,
    session_id   TEXT REFERENCES sessions(session_id) ON DELETE CASCADE,
    event_index  INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    content      JSONB DEFAULT '{}',
    metadata     JSONB DEFAULT '{}',
    timestamp    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, event_index)
);

CREATE INDEX idx_events_session ON events(session_id, event_index);
CREATE INDEX idx_sessions_account ON sessions(account_id, status);
CREATE INDEX idx_bindings_lookup ON account_bindings(channel_type, channel_user_id);
```

---

## 8. docker-compose 服务拓扑

```
                    app-network (bridge)
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼───────┐  ┌───────▼───────┐  ┌───────▼───────┐
│   hpagent     │  │   napcat      │  │  temporal      │
│   :8082       │  │   :6099       │  │  :7233         │
│   sysbox-runc │  │   QQ bot      │  │  workflow      │
└───────┬───────┘  └───────────────┘  └───────┬───────┘
        │                                      │
        │  AccountService                      │  Temporal DB
        │  SessionManager                      │
        │  ChannelRouter ──→ NapCatChannel     │
        │                                      │
        ├──────────┬───────────────────────────┤
        │          │                           │
┌───────▼───┐ ┌───▼──────────┐  ┌─────────────▼──────┐
│ hpagent-  │ │ temporal-    │  │ temporal-web       │
│ postgresql│ │ postgresql   │  │ :8088              │
│ :5432     │ │ :5432        │  │ Temporal Web UI    │
│ app data  │ │ Temporal内部  │  └────────────────────┘
└───────────┘ └──────────────┘
```

---

## 9. 代码改动索引

| 改动 | 文件 | 行数 |
|------|------|------|
| Account 模块（新增） | `src/account/models.py`, `account_service.py`, `__init__.py` | ~80 |
| ChannelRouter（新增） | `src/sandbox/channels/router.py` | ~25 |
| PG 仓库（新增） | `src/session/repositories.py` 追加 | ~200 |
| DB 迁移（新增） | `migrations/001_accounts.sql` | ~40 |
| types.py account_id | `src/common/types.py` | +3 字段 |
| Session account_id | `src/session/models.py` | +1 字段 |
| Worker 入口重构 | `src/orchestration/worker.py` | ~60 改动 |
| Workflow 长期运行 | `src/orchestration/workflow.py` | ~80 改动 |
| Activities ChannelRouter | `src/harness/activities.py` | ~10 改动 |
| ContextBuilder 跨渠道提示 | `src/harness/context_builder.py` | +15 |
| SessionManager account_id | `src/session/session_manager.py` | +5 |
| docker-compose PG | `docker-compose.yaml` | +12 |
| 导出更新 | 各 `__init__.py` | ~10 |
| 依赖 | `src/requirements.txt` | +1 (asyncpg) |

**总计约 550 行新增/改动**，核心是在 v4 四层架构上增加 Account 解析层、ChannelRouter 路由层，并将 Workflow 从一次性执行改为长期运行。
