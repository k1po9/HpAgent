# HpAgent .data 目录数据总览

`.data/` 是 HpAgent 所有运行时数据的根目录。宿主机和 Docker 容器通过 `WORKSPACE_ROOT` 环境变量和卷挂载共享同一个 `.data/` 目录。

## 目录树

```
.data/
├── accounts.json                          # 账号绑定持久化
├── logs/                                  # 日志文件
│   ├── hpagent.jsonl                     # 结构化 JSON 日志（DEBUG+）
│   └── hpagent-error.log                 # 纯文本错误日志（ERROR+）
├── sessions/                              # 会话 JSONL 备份
│   └── {session_id}.jsonl               # 每行一个 JSON 记录
├── workspace/                             # 工作区根目录
│   ├── workspace.db                      # SQLite 数据库（users + sessions）
│   └── {user_uuid}/                      # 用户目录
│       ├── user_profile.yaml             # 用户偏好
│       ├── skills/                       # 用户技能（预留，空）
│       ├── persistent/                   # 用户持久文件（跨会话保留）
│       └── sessions/
│           └── {session_id}/
│               ├── session.yaml          # 会话元数据
│               └── workspace/
│                   ├── input/            # 工具输入文件
│                   ├── scratch/          # 临时/暂存区
│                   └── output/           # 工具输出文件
└── napcat/                               # NapCat QQ 机器人数据（Docker 容器管理）
    ├── data/                             # QQ 登录凭证
    ├── cache/                            # 缓存数据
    └── config/                           # 配置（passkey 等）
```

---

## 1. accounts.json — 账号绑定

**创建者**：`AccountService._save()`（`src/account/account_service.py`）
**触发时机**：首次收到未知渠道用户消息时（`resolve()`），或显式绑定渠道（`bind_channel()`）
**写入方式**：原子写入 —— 先写 `.tmp` 再 `os.replace()`

```python
# src/orchestration/worker.py:265
account_service = AccountService(data_dir=Path(config.workspace.root).parent)
# workspace.root = ".data/workspace"，parent = ".data"
# → accounts.json 落在 .data/accounts.json
```

### 示例内容

```json
{
  "550e8400-e29b-41d4-a716-446655440000": {
    "account_id": "550e8400-e29b-41d4-a716-446655440000",
    "bindings": {
      "napcat": "2109279314",
      "web": "user_abc"
    },
    "created_at": 1717000000.0,
    "updated_at": 1717000000.0
  }
}
```

### 数据模型

```python
# src/account/models.py
@dataclass
class Account:
    account_id: str                          # UUID v4
    bindings: Dict[str, str]                 # 渠道类型 → 渠道用户ID
    created_at: float                        # Unix 时间戳
    updated_at: float                        # Unix 时间戳
```

### 代码示例

```python
from pathlib import Path
from account.account_service import AccountService

svc = AccountService(data_dir=Path(".data"))
# 首次收到 QQ 用户 "2109279314" 的消息
account_id = await svc.resolve("napcat", "2109279314")
# → 创建 UUID，写入 .data/accounts.json

# 为已有账号追加 Web 渠道绑定
await svc.bind_channel(account_id, "web", "user_abc")

# 列出所有账号
all_ids = svc.list_all_ids()
# → ["550e8400-...", "660e8400-..."]
```

---

## 2. logs/ — 日志文件

**创建者**：`setup_logging()`（`src/common/logging.py`）
**调用时机**：`main.py` 启动时，在其他模块之前
**轮转策略**：每日午夜轮转，保留 30 天

```python
# src/main.py:46-47
_log_dir = Path(os.getenv("LOG_DIR", str(_project_root / ".data/logs")))
setup_logging(level=_log_level, log_dir=_log_dir)
```

### 2a. hpagent.jsonl — 结构化全量日志

记录级别 `DEBUG` 及以上，每行一个 JSON 对象。

```python
# src/common/logging.py:24-35
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            payload["exc"] = str(record.exc_info[1])
        if record.args and isinstance(record.args, dict):
            payload["extra"] = record.args
        return json.dumps(payload, ensure_ascii=False, default=str)
```

示例内容：

```jsonl
{"ts": "2026-05-31T13:41:00", "level": "INFO", "logger": "HpAgent", "msg": "HpAgent starting on task_queue='hpagent-task-queue'"}
{"ts": "2026-05-31T13:41:01", "level": "INFO", "logger": "HpAgent.OrchestrationWorker", "msg": "Redis connected: redis://redis:6379"}
{"ts": "2026-05-31T13:41:01", "level": "INFO", "logger": "HpAgent.OrchestrationWorker", "msg": "HindsightClient initialized: base_url=http://hindsight:8888"}
{"ts": "2026-05-31T13:41:02", "level": "WARNING", "logger": "HpAgent.SessionStore", "msg": "Redis unavailable, falling back to in-memory storage", "extra": {"url": "redis://..."}}
{"ts": "2026-05-31T13:42:00", "level": "INFO", "logger": "HpAgent.OrchestrationWorker", "msg": "New account 550e8400-... bound to napcat:2109279314"}
{"ts": "2026-05-31T13:42:01", "level": "INFO", "logger": "HpAgent.OrchestrationWorker", "msg": "Started new session session-550e8400-... (account=550e8400-...)"}
```

### 2b. hpagent-error.log — 纯文本错误日志

仅记录 `ERROR` 及以上。

```
2026-05-31 13:45:00 [ERROR] HpAgent.OrchestrationWorker: Failed to connect to Temporal: Connection refused
```

---

## 3. workspace/workspace.db — SQLite 数据库

**创建者**：`WorkspaceDB._ensure_schema()`（`src/session/db.py`）
**初始化**：`worker.py:221`

```python
# src/orchestration/worker.py:219-221
workspace_root = Path(config.workspace.root)  # ".data/workspace"
workspace_db = WorkspaceDB(
    config.workspace.db_path or str(workspace_root / "workspace.db")
)
# → .data/workspace/workspace.db
```

### Schema

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    uuid TEXT PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    profile_path TEXT NOT NULL DEFAULT '',
    persistent_dir TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_uuid TEXT NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active',
    task_summary TEXT NOT NULL DEFAULT '',
    session_dir TEXT NOT NULL DEFAULT '',
    plan_file TEXT NOT NULL DEFAULT '',
    conversation_file TEXT NOT NULL DEFAULT '',
    output_dir TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created
    ON sessions(user_uuid, created_at DESC);
```

### 代码示例

```python
from session.db import WorkspaceDB

db = WorkspaceDB(".data/workspace/workspace.db")

# 插入/更新用户
db.upsert_user(
    uuid="550e8400-...",
    username="",
    profile_path="550e8400-.../user_profile.yaml",
    persistent_dir="550e8400-.../persistent",
)

# 插入会话
db.insert_session(Session(
    session_id="session-550e8400-...",
    account_id="550e8400-...",
    user_uuid="550e8400-...",
    session_dir="550e8400-.../sessions/session-550e8400-...",
    output_dir="550e8400-.../sessions/session-550e8400-.../workspace/output",
))

# 查询会话
session = db.get_session("session-550e8400-...")
# → Session 对象或 None

# 标记会话完成
db.complete_session("session-550e8400-...")
```

示例数据：

```sql
-- users 表
uuid: "550e8400-e29b-41d4-a716-446655440000"
username: ""
profile_path: "550e8400-e29b-41d4-a716-446655440000/user_profile.yaml"
persistent_dir: "550e8400-e29b-41d4-a716-446655440000/persistent"
created_at: "2026-05-31 13:41:00"

-- sessions 表
session_id: "session-550e8400-e29b-41d4-a716-446655440000"
user_uuid: "550e8400-e29b-41d4-a716-446655440000"
status: "active"
task_summary: "帮我写一个 Python 脚本"
session_dir: "550e8400-.../sessions/session-550e8400-..."
output_dir: "550e8400-.../sessions/session-550e8400-.../workspace/output"
tags: "[]"
created_at: "2026-05-31 13:42:00"
```

> **注意**：`plan_file` 和 `conversation_file` 是架构预留字段，当前无代码写入内容。

---

## 4. workspace/{user_uuid}/ — 用户工作区目录

**创建者**：`init_user()` + `init_session()`（`src/session/workspace.py`）
**调用链**：`worker.py:388-399` 每次收到消息时

```python
# src/orchestration/worker.py:386-399
workspace_path = str(
    deps.workspace_root / account_id / "sessions" / session_id / "workspace"
)
init_user(deps.file_store, deps.workspace_db, account_id)
init_session(
    deps.file_store, deps.workspace_db,
    user_uuid=account_id,
    session_id=session_id,
    task_summary=message.content[:100],
)
```

### 4a. user_profile.yaml

`init_user()` 在文件不存在时创建。

```python
# src/session/workspace.py:44-51
profile_rel = f"{user_uuid}/user_profile.yaml"
if not file_store.exists_sync(profile_rel):
    _write_yaml(file_store, profile_rel, {
        "user_uuid": user_uuid,
        "username": username,
        "preferences": {},
        "created_at": _now_iso(),
    })
```

示例内容：

```yaml
user_uuid: "550e8400-e29b-41d4-a716-446655440000"
username: ""
preferences: {}
created_at: "2026-05-31T13:41:00.000000+00:00"
```

### 4b. session.yaml

`init_session()` 每次新会话创建（幂等，已存在则跳过）。

```python
# src/session/workspace.py:93-101
_write_yaml(file_store, f"{session_rel}/session.yaml", {
    "session_id": session_id,
    "user_uuid": user_uuid,
    "status": "active",
    "task_summary": task_summary,
    "tags": tags or [],
    "created_at": now,
})
```

示例内容：

```yaml
session_id: "session-550e8400-e29b-41d4-a716-446655440000"
user_uuid: "550e8400-e29b-41d4-a716-446655440000"
status: "active"
task_summary: "帮我写一个 Python 脚本"
tags: []
created_at: "2026-05-31T13:42:00.000000+00:00"
```

### 4c. workspace/input/ scratch/ output/

三个空目录，由 `init_session()` 创建，用于沙箱工具的文件 I/O：

```python
# src/session/workspace.py:86-91
session_rel = f"{user_uuid}/sessions/{session_id}"
for sub in [
    f"{session_rel}/workspace/input",
    f"{session_rel}/workspace/scratch",
    f"{session_rel}/workspace/output",
]:
    file_store.mkdir_sync(sub)
```

沙箱工具（`fs_read`、`fs_write`、`fs_edit`、`bash` 等）绑定到 `workspace_path`，所有文件操作被限定在此目录内。

---

## 5. sessions/{session_id}.jsonl — 会话备份

**创建者**：`SessionStore._backup_to_file()`（`src/session/store.py`）
**触发时机**：每次记忆提取（`retain_memories()`）和会话归档（`archive()`）
**写入方式**：追加一行 JSON

```python
# src/orchestration/worker.py:271-276
backup_store = LocalFileStore(root=Path(config.session.backup_dir))
# backup_dir = ".data/sessions"
session_store = SessionStore(
    redis_cache=redis_cache,
    hindsight_client=hindsight_client,
    file_store=backup_store,
)
```

```python
# src/session/store.py:394-403
record = {
    "type": "archive" if is_final else "retain",
    "session_id": session_id,
    "timestamp": time.time(),
    "session_meta": session.to_dict() if session else None,
    "events": (
        [e.to_dict() if isinstance(e, Event) else e for e in events]
        if events else None
    ),
}
line = json.dumps(record, ensure_ascii=False) + "\n"
await self._file_store.append_line(f"{session_id}.jsonl", line)
```

示例内容：

```jsonl
{"type": "retain", "session_id": "session-550e8400-...", "timestamp": 1717000000.0, "session_meta": null, "events": [{"role": "user", "content": "你好"}]}
{"type": "retain", "session_id": "session-550e8400-...", "timestamp": 1717000005.0, "session_meta": null, "events": [{"role": "user", "content": "什么是 Python？"}, {"role": "assistant", "content": "Python 是..."}]}
{"type": "archive", "session_id": "session-550e8400-...", "timestamp": 1717000100.0, "session_meta": {"session_id": "session-550e8400-...", "account_id": "550e8400-...", "status": "completed", "creator_id": "2109279314", "channel_type": "napcat", "task_summary": "帮我写一个 Python 脚本", ...}, "events": [...]}
```

### Redis 热数据（补充）

同一份事件数据同时写入 Redis（高可用时），键设计：

```
session:{session_id}:events    → List   事件流（RPUSH 追加 / LRANGE 读取）
session:{session_id}:meta      → Hash   会话元数据
account:{account_id}:active    → String 指向当前活跃 session_id
```

Redis 不可用时静默降级为内存 dict，不阻塞主流程。

---

## 6. napcat/ — NapCat QQ 机器人数据

由 Docker Compose 卷挂载，NapCat 容器管理，HpAgent 代码不直接写入。

```yaml
# docker-compose.yaml
volumes:
  - ./.data/napcat/data:/app/.napcat       # QQ 登录凭证
  - ./.data/napcat/cache:/app/data/cache   # 缓存数据
  - ./.data/napcat/config:/app/napcat/config  # 配置（passkey 等）
```

---

## 7. Session 数据模型参考

```python
# src/session/models.py
class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"

@dataclass
class Session:
    session_id: str                    # "session-{user_uuid}"
    account_id: str = ""               # = user_uuid
    status: SessionStatus = SessionStatus.ACTIVE
    creator_id: str = ""               # 渠道原始 sender_id（如 QQ 号）
    channel_type: str = "console"      # "napcat" | "console" | "web"
    task_summary: str = ""             # 首条消息前 100 字符
    session_dir: str = ""              # 相对路径: "{uuid}/sessions/{sid}"
    plan_file: str = ""                # 预留，未使用
    conversation_file: str = ""        # 预留，未使用
    output_dir: str = ""               # 相对路径: "{uuid}/sessions/{sid}/workspace/output"
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0            # Unix 时间戳
    updated_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
```

---

## 完整初始化流程

```
main.py 启动
  ├── setup_logging(log_dir=".data/logs")
  │     → 创建 .data/logs/hpagent.jsonl + hpagent-error.log
  │
  └── start_worker(config)
        └── init_dependencies(config)
              ├── workspace_root = ".data/workspace"
              ├── WorkspaceDB(".data/workspace/workspace.db")
              │     → 创建/打开 SQLite，执行 _ensure_schema()
              ├── AccountService(data_dir=".data")
              │     → 加载 .data/accounts.json（不存在则跳过）
              └── SessionStore(backup_store=LocalFileStore(".data/sessions"))
                    → 后续 retain/archive 时写入 .data/sessions/{sid}.jsonl

收到每条消息时:
  handle_message(message)
    ├── account_service.resolve(channel_type, sender_id)
    │     → 首次则创建 UUID，写入 .data/accounts.json
    ├── init_user(file_store, db, account_id)
    │     → 创建 .data/workspace/{uuid}/ 目录 + user_profile.yaml
    │     → db.upsert_user(...)
    └── init_session(file_store, db, user_uuid, session_id, ...)
          → 创建 .data/workspace/{uuid}/sessions/{sid}/ 子目录 + session.yaml
          → db.insert_session(...)

会话结束时:
  SessionStore.archive()
    → 追加 {"type": "archive", ...} 到 .data/sessions/{sid}.jsonl
    → Redis: 更新 session:{sid}:meta status=completed
```

---

## 关键行为说明

| 特性 | 说明 |
|---|---|
| **accounts.json 原子写** | 全量序列化到 `.tmp`，然后 `os.replace()`，不会出现半写 |
| **workspace.db WAL 模式** | 支持并发读写，崩溃后自动恢复 |
| **session.yaml 幂等** | `init_session()` 在 session_id 已存在时跳过，不覆盖已有文件 |
| **JSONL 追加写** | 每行独立 JSON，可安全 `tail -f` 或逐行解析，不担心截断 |
| **日志按天轮转** | 保留 30 天历史，自动清理过期文件 |
| **file_store vs backup_store** | 两个独立的 `LocalFileStore`，根目录分别为 `workspace/` 和 `sessions/`，路径不会交叉 |
