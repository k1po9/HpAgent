# HpAgent 冗余设计与繁琐代码分析报告

> 审查范围: 58 个 Python 源文件（~13,400 行），不含 `src/agent/` 目录。
> 审查日期: 2026-05-25
> 最后更新: 2026-05-25（Phase 4 清理完成）

## 清理进度

| 类别 | 状态 |
|------|------|
| `src/sandbox/tools/` 删除 | ✅ 已完成 |
| `harness/runner.py` 旧工具路径移除 | ✅ 已完成 |
| `common/interfaces.py:ITool` 删除 | ✅ 已完成 |
| `tools/executor.py:BaseToolExecutor` 删除 | ✅ 已完成 |
| `harness/runner.py:_resolve_tags_match()` 删除 | ✅ 已完成 |
| `sandbox/nsjail.py` Redis 持久化移除 | ✅ 已完成 |
| `storage/protocols.py` 精简 | ✅ 已完成 |
| `LocalFileStore` 实现并接入 SessionStore | ✅ 已完成 |
| `ContextBuilder` lru_cache 缓存 | ✅ 已完成 |
| `config/models.yaml` RAG 默认启用 | ✅ 已完成 |
| `orchestration/config.py` 新增 ToolRagConfig/McpConfig/SkillsConfig | ✅ 已完成 |
| `worker.py` hasattr 模式替换为 dataclass 属性 | ✅ 已完成 |
| `src/__init__.py` 文档更新 | ✅ 已完成 |

---

## 一、双重工具体系（最大冗余点）✅ 已清理

> **状态: 已完成。** `src/sandbox/tools/` 目录（4 文件，~530 行）已彻底删除，`harness/runner.py` 中 `_get_tools()` 和 `_execute_tool()` 的旧路径分支已移除。

### 1.1 现状

项目中存在两套完整的工具系统：

| 维度 | 旧系统 `src/sandbox/tools/` | 新系统 `src/tools/` |
|------|--------------------------|-------------------|
| 文件数 | 4 个 (~530 行) | 9 个 (~950 行) |
| Tool 基类 | `sandbox.tools.base.BaseTool` (自建 ABC) | `langchain_core.tools.BaseTool` |
| 注册表 | `sandbox.tools.registry.ToolRegistry` (单槽) | `tools.registry.ToolRegistry` (三槽 + RAG + freeze) |
| 工具结果 | `ToolResult` (重导出自 tools) | `tools.types.ToolResult` |
| 工厂 | `ToolFactory` + `DynamicTool` | `@tool` 装饰器 + `StructuredTool.from_function` |
| 状态 | **已废弃** (导入触发 DeprecationWarning) | 当前使用 |

### 1.2 废弃系统的自我宣告

`src/sandbox/tools/__init__.py` 第 2-10 行:

```python
"""
Tools —— DEPRECATED: 此包已被 src/tools/ 模块替代。

请使用:
  from langchain_core.tools import BaseTool     # 替代自建 BaseTool
  from tools.types import ToolResult            # 替代自建 ToolResult
  from tools.registry import ToolRegistry       # 替代自建 ToolRegistry
  from tools.builtin import calculator_tool, ...  # 替代 ToolFactory
"""
```

**每个废弃文件** (`base.py`、`registry.py`、`factory.py`) 的模块顶部都在重复打印相同的 deprecation 警告。

### 1.3 废弃系统对运行时代码的污染

`harness/runner.py` 中的 `_get_tools()` 和 `_execute_tool()` 各自维护"新路径优先、旧路径兜底"的双分支：

```
_get_tools()     →  if self._tool_registry is not None: → 新路径 (ToolRegistry)
                      else:                               → 旧路径 (遍历 sandbox 列表)

_execute_tool()  →  if self._tool_registry is not None: → 新路径 (ToolRegistry.execute)
                      else:                               → 旧路径 (遍历 sandbox 列表)
```

每次工具调用都经过 if/else，旧路径的异常处理用裸 `except Exception` 吞没错误。

### 1.4 建议

- 彻底删除 `src/sandbox/tools/` 整个目录（base.py、registry.py、factory.py、`__init__.py`）
- 移除 `harness/runner.py` 中 `_get_tools()` 和 `_execute_tool()` 的旧路径分支
- 如果担心回归，保留 1-2 个版本的 git tag 即可回溯

---

## 二、三个 ToolResult 类型并存

### 2.1 现状

项目中存在三个名为 `ToolResult` 的类型，结构各不相同：

**类型 A — `common/types.py:232`**（事件流中的工具结果）

```python
@dataclass
class ToolResult:
    tool_call_id: str            # 关联的 ToolCall.id
    status: str                  # "success" / "error"
    content: Any = ""            # 成功时的返回数据
    error: Optional[str] = None  # 失败时的错误信息
```

用途：作为 `Event.content` 的一部分存储在事件流中。字段语义是"调用结果记录"，侧重可追溯性。

**类型 B — `tools/types.py:12`**（新工具体系统一返回值）

```python
@dataclass
class ToolResult:
    success: bool = True
    output: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
```

用途：工具执行后的返回值，在 `ToolRegistry.execute()` 中构造。字段语义是"执行结果"，侧重结构化传递。

**类型 C — `sandbox/tools/base.py:72`**（废弃层重导出）✅ 已随 `sandbox/tools/` 一并删除

```python
from tools.types import ToolResult as _NewToolResult
ToolResult = _NewToolResult  # re-export
```

等于类型 B，仅为了向后兼容而存在。

### 2.2 问题

类型 A 和类型 B **名称相同但字段完全不可互换**：

| 字段 | 类型 A (common) | 类型 B (tools) |
|------|----------------|----------------|
| 关联 ID | `tool_call_id: str` | 无 |
| 成功标记 | `status: str` ("success"/"error") | `success: bool` |
| 结果数据 | `content: Any` | `output: Any` |
| 错误信息 | `error: Optional[str]` | `error: Optional[str]`（相同） |
| 元数据 | 无 | `metadata: dict` |

同一行代码 `from common.types import ToolResult` 和 `from tools.types import ToolResult` 导入的是两个**不能互换**的类型。这极易导致运行时类型错误。

### 2.3 建议

- 将 `common/types.py` 中的类型重命名为 `ToolCallEvent`（明确其"事件记录"语义），避免与执行结果混淆
- 删除类型 C（随废弃工具体系一起移除）

---

## 三、三重 Tool 抽象接口 ✅ 已清理

> **状态: 已完成。** `ITool` 从 `common/interfaces.py` 删除，废弃的 `BaseTool` 随 `src/sandbox/tools/` 一并移除。

### 3.1 现状

**接口 A — `common/interfaces.py:ITool`**（193-226 行）

```python
class ITool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    @property
    @abstractmethod
    def description(self) -> str: ...
    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]: ...
    @abstractmethod
    async def execute(self, **kwargs) -> Any: ...
```

**零实现、零引用**。在整个代码库中，没有任何类继承 `ITool`，没有任何函数参数标注为 `ITool`。它是一个纯粹的孤立抽象。

**接口 B — `sandbox/tools/base.py:BaseTool`**（75-139 行）

与 `ITool` 几乎相同，增加了 `tool_type` 属性和 `get_definition()` / `get_openai_format()` 方法。标注为 DEPRECATED。

**接口 C — `langchain_core.tools.BaseTool`**

实际使用的接口。内置于 `tools/registry.py` 和 `tools/builtin/` 各工具。

### 3.2 建议

- 直接删除 `common/interfaces.py` 中的 `ITool` 类
- 废弃的 `BaseTool` 随 `src/sandbox/tools/` 一起移除

---

## 四、存储层过度抽象 ✅ 已清理

> **状态: 已完成。** `FileStore` Protocol 保留（`LocalFileStore` 已实现），`PubSub` Protocol 删除，`StoreErrorCode` 精简为仅 `NOT_FOUND`，`LocalFileStore` 已接入 `SessionStore` 和 `worker.py`。

### 4.1 现状

`src/storage/protocols.py`（~220 行）定义了：

| Protocol | 方法数 | 实现情况 |
|----------|--------|---------|
| `KeyValueStore` | 4 (get/set/delete/list) | `storage/redis.py:RedisCache` 实现 |
| `FileStore` | 4 (read/write/delete/list) | **零实现** |
| `PubSub` | 3 (publish/subscribe/unsubscribe) | **零实现** |

附带完整的错误体系：

```python
class StoreErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    DUPLICATE = "DUPLICATE"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_DATA = "INVALID_DATA"

class StoreError(Exception):
    def __init__(self, code: StoreErrorCode, message: str, original: Exception | None = None):
        ...
```

这套错误体系在整个项目中**只被 `RedisCache` 使用**，且只抛出了 `StoreErrorCode.NOT_FOUND`。其余 4 个错误码从未被触发。

### 4.2 实际文件操作方式

项目中的文件操作并未通过 `FileStore` 协议，而是直接用标准库：

```python
# session/store.py:419 —— 直接用 pathlib + open
filepath = self._backup_dir / f"{session_id}.jsonl"
with open(filepath, "a", encoding="utf-8") as f:
    f.write(line)

# workspace/manager.py —— 直接用 pathlib.mkdir
self._root.mkdir(parents=True, exist_ok=True)
```

这说明 `FileStore` 协议从一开始就是不必要的一层。

### 4.3 建议

- 删除 `FileStore` 和 `PubSub` 两个 Protocol
- 保留 `KeyValueStore` 和 `StoreError`（它们至少有一个真实实现）
- 将未使用的 `StoreErrorCode` 枚举值移除，只保留 `NOT_FOUND`

---

## 五、SessionStore 的重复 Redis 降级模式

### 5.1 现状

`session/store.py` 中所有 CRUD 方法都遵循同一个 7 行模板：

```python
if self._cache:                          # Redis 可用?
    try:
        ...redis 操作...
    except Exception as e:
        logger.warning("DEGRADATION: ... (%s) → falling back to memory", e)
...内存 dict 回退...
```

这个模板在以下方法中**精确重复了 7 次**：

| 方法 | 行数 |
|------|------|
| `create_session()` | 93-108 |
| `get_session()` | 113-120 |
| `get_active_session_id()` | 124-131 |
| `update_status()` | 140-149 |
| `archive()` | 158-163 |
| `append_events()` | 178-186 |
| `get_events()` | 199-207 |

每次都是相同的 try/except/log/fallback 模式，仅内部 Redis 操作不同。总计 ~80 行模板代码。

### 5.2 内存回退无清理机制

```python
self._mem_events: Dict[str, List[Event]] = {}     # 无限增长
self._mem_sessions: Dict[str, Session] = {}        # 无限增长
self._mem_active: Dict[str, str] = {}              # 仅 archive 时 pop
```

`archive()` 方法清理了 `_mem_active`，但 `_mem_events` 和 `_mem_sessions` 永不清理。长时间运行会持续泄漏内存。

### 5.3 建议

- 提取 `_with_redis(key_pattern, fallback_func)` 装饰器或辅助方法统一降级模式
- 为内存回退字典添加 LRU 或 TTL 清理

---

## 六、`_resolve_tags_match()` — 假逻辑伪装的死代码 ✅ 已清理

> **状态: 已完成。** 方法已删除，两处调用点改为直接使用字面量 `"any_strict"`。

### 6.1 现状

`harness/runner.py:274-287`：

```python
@staticmethod
def _resolve_tags_match(metadata: Dict[str, Any]) -> str:
    scope = metadata.get("detail_type", "")
    if scope == "private":
        return "any_strict"
    if scope == "group":
        return "any_strict"
    return "any_strict"
```

三个分支全部返回 `"any_strict"`。该函数等效于 `return "any_strict"`，14 行代码（含 docstring）无任何实际逻辑。

### 6.2 连锁浪费

不仅函数本身是死代码，它的存在还驱动了一系列无意义的数据传递：

```
napcat.normalize_message() → metadata["detail_type"] = "private" / "group"
  → worker.handle_message() → metadata 原样传递
    → runner.process_turn() → metadata 提取 → _resolve_tags_match(metadata)
      → 永远返回 "any_strict"
```

从渠道层的 `detail_type` 分类到 runner 层的标签匹配策略选择，整条链路的结果是一个常量。如果未来真的需要根据对话场景切换匹配策略，届时再加上即可。

### 6.3 建议

- 直接删除 `_resolve_tags_match()`，调用处改为 `"any_strict"`
- 如果 `detail_type` 在 metadata 中有其他用途则保留传递，否则可简化

---

## 七、NapCat `normalize_message()` — 163 行巨型级联

### 7.1 现状

`src/sandbox/channels/napcat.py:86-248`，`normalize_message()` 方法结构：

```
post_type 分支 (4 路):
  ├── "message"    → 消息事件（私聊/群聊）
  ├── "notice"     → 通知事件（11 路子类型）
  │     ├── group_upload
  │     ├── group_admin
  │     ├── group_decrease
  │     ├── group_increase
  │     ├── group_ban
  │     ├── group_recall
  │     ├── poke
  │     ├── friend_add
  │     └── ...
  ├── "request"    → 好友/群请求
  └── "meta_event" → 心跳/生命周期
```

`"notice"` 分支（172-200 行）包含一个 11 路 if/elif 链，每个分支从 OneBot JSON 的不同字段提取 `sender_id`：

```python
if notice_type == "group_upload":
    sender_id = str(data.get("user_id", ""))
elif notice_type == "group_admin":
    sender_id = str(data.get("user_id", ""))
elif notice_type == "group_decrease":
    sender_id = str(data.get("user_id", ""))
elif notice_type == "group_increase":
    sender_id = str(data.get("user_id", ""))
# ... 更多分支 ...
```

大部分分支实际上是 `str(data.get("user_id", ""))`，只有少数特殊类型从 `operator_id` 或 `target_id` 取。整个链可以用一个查找表替代：

```python
_SENDER_FIELD_MAP = {
    "group_upload": "user_id",
    "group_admin": "user_id",
    "group_ban": "operator_id",    # 特殊
    "poke": "target_id",           # 特殊
    # ...
}
```

### 7.2 可变 metadata dict 贯穿所有分支

所有分支共用同一个 `metadata` dict，不同分支向其中插入不同的键。阅读者需要追踪 160 行代码才能确认某个键在哪些条件下被设置，增加了理解成本。

### 7.3 建议

- 将 `notice_type` 分支重构为基于查找表的模式
- 拆分为 `_normalize_message_event()` / `_normalize_notice_event()` / `_normalize_request_event()` 三个独立方法

---

## 八、HarnessRunner 的长方法与方法间重复

### 8.1 `process_turn()` — 201 行上帝方法

`harness/runner.py:68-268`，结构：

```
process_turn():
  ├── 解构 user_message (10 行)
  ├── 确保会话 (1 行)
  ├── 追加用户事件 (10 行)
  ├── 加载历史 (1 行)
  ├── 构建 turn_events (3 行)
  ├── [multi-agent 路径] (35 行)
  │     └── recall_memories(10 个参数)
  └── [single-agent 路径] (90 行)
        └── while 循环:
              ├── recall_memories(10 个参数) ← 与 multi 路径完全相同
              ├── 构建上下文
              ├── 获取工具列表
              ├── 调用模型
              ├── 追加模型事件 (15 行)
              ├── 处理工具调用 (25 行)
              └── continue/break
```

两个路径中 `recall_memories()` 的调用参数完全相同（10 个参数逐字复制）。模型事件构造逻辑在 multi 路径和 single 路径中各写了一遍。

### 8.2 `_send_response()` 参数冗余

`harness/runner.py:395-419`：

```python
async def _send_response(self, content: str, user_message: Dict[str, Any]) -> bool:
    ch_type_str = user_message.get("channel_type", "console")
    ch_type = self._resolve_channel(ch_type_str)
    msg = UnifiedMessage(
        session_id=user_message["session_id"],
        account_id=user_message["account_id"],
        sender_id=user_message["sender_id"],
        channel_type=ch_type,
        content=content,
        metadata=user_message.get("metadata", {}),
    )
```

`user_message` 的 6 个字段在此处被逐一展开，只是为了构造一个 `UnifiedMessage`。这是典型的"拆包再组包"反模式。可以直接传递 `UnifiedMessage` 或让调用方自己构造。

### 8.3 每轮 INFO 级别打印完整上下文

`harness/runner.py:180-182`：

```python
for turn in context:
    logger.info("Context turn: %s", turn)
```

在 while 循环内（每轮 tool turn 执行一次），将整个上下文（system prompt + 20+ 轮对话消息）逐条以 INFO 级别打印。假设 20 tool turns × 40 messages = 800 行日志。应为 DEBUG 级别或直接移除。

### 8.4 建议

- 提取 `_recall_memories_for_turn()` 统一两处记忆召回调用
- 提取 `_record_model_event()` 统一模型事件构造+追加
- 上下文打印改为 `logger.debug` 或移除
- `_send_response()` 改为接收 `UnifiedMessage` 而非 dict

---

## 九、Worker 层巨型函数 ✅ 部分清理

> **状态: Config 层面已简化。** `hasattr(config, "tool_rag")` 等模式已替换为 `config.models.tool_rag.enabled` 等 dataclass 属性直接访问。巨型函数拆分仍待后续处理。

### 9.1 `init_dependencies()` — 164 行顺序初始化

`orchestration/worker.py:167-330`，函数签名：

```python
async def init_dependencies(config: AppConfig) -> tuple[
    HarnessRunner, AccountService, ChannelRouter, SandboxManager, WorkspaceManager
]:
```

返回类型是一个 5 元组，调用方通过位置解构获取：

```python
harness_runner, account_service, channel_router, sandbox_manager, workspace_manager = \
    await init_dependencies(config)
```

内部按顺序初始化 12+ 个依赖，每个都有独立的错误处理策略：有时捕获 Exception 并降级，有时不捕获让异常传播。这种不一致意味着某些初始化失败会静默降级（如 Hindsight），而另一些会直接崩溃。

### 9.2 `setup_tools()` — 4 个关注点混在一个函数

`orchestration/worker.py:54-150`，97 行函数同时处理：
- RAG 检索器初始化
- 内置工具注册
- MCP Server 连接
- Skills 加载

每个都用 `try/except` 包裹，但错误处理方式各有不同。且这些功能大多数默认关闭（`tool_rag.enabled: false`, `mcp.auto_connect: false`），但仍然执行配置提取和判空逻辑。

### 9.3 两个 Schedule 创建函数复制粘贴

`_setup_reflect_schedule()` (455-499 行) 和 `_setup_metrics_schedule()` (502-537 行)：

```
相同:
  - from temporalio.client import Schedule, ScheduleActionStartWorkflow, ...
  - schedule_id = "hpagent-xxx-schedule"
  - client.create_schedule(schedule_id, Schedule(...))
  - try/except 捕获 "may already exist"
  - logger.info(...)

不同:
  - schedule_id 字符串
  - Workflow 类名
  - 时间间隔参数
```

两个 40+ 行的函数共享约 35 行相同结构，仅 5 行不同。应提取为 `_create_periodic_schedule(client, schedule_id, workflow_cls, interval, ...)`。

### 9.4 建议

- 拆分 `init_dependencies()` 为 `_init_models()` / `_init_storage()` / `_init_channels()` / `_init_memory()` 等
- 用 dataclass 替代 5 元组作为返回值
- `setup_tools()` 拆分为 `_init_rag()` / `_load_builtin_tools()` / `_load_mcp()` / `_load_skills()`
- 合并两个 Schedule 创建函数

---

## 十、Hindsight 客户端的效率问题

### 10.1 双重 recall 调用

`session/store.py:229-286`，`recall_memories()` 方法：

```python
items = await self._hindsight.recall(...)          # 第 1 次 HTTP 请求
formatted = await self._hindsight.recall_formatted(...)  # 内部又调 recall() → 第 2 次 HTTP 请求
```

`recall_formatted()` 定义在 `hindsight_client.py:543`，内部第 573 行重新调用 `self.recall(...)`，参数完全相同。每次对话轮次都会向 Hindsight API 发送**两次相同的召回请求**。

### 10.2 工具循环中重复召回

`harness/runner.py:161`，`recall_memories(query=user_content, ...)` 在 while 循环**内部**调用：

```python
while turns_taken < self._max_tool_turns:   # 最多 20 轮
    turns_taken += 1
    memories_items, memories_text = await self._session.recall_memories(
        query=user_content, ...  # query 始终是原始用户消息
    )
```

`user_content` 在整个循环中不变，但每轮 tool turn 都重新调用一次。一个需要 5 轮工具调用的请求会触发 5 次（实际上是 5×2=10 次，因为双重召回）Hindsight API 调用。

### 10.3 无 HTTP 连接池

三处代码各自创建新的 `httpx.AsyncClient`：

| 文件 | 行号 | 调用频率 |
|------|------|---------|
| `resources/model_client.py` | 79 | 每轮 LLM 调用 |
| `memory/hindsight_client.py` | 192 | 每次 recall/retain/reflect |
| `resources/resource_pool.py` | 116 | 每次代理请求 |

每次 `async with httpx.AsyncClient() as client:` 都创建新的 TCP 连接并进行 TLS 握手。应改为模块级共享的 `httpx.AsyncClient` 实例，启用连接池 (`limits=httpx.Limits(max_keepalive_connections=10)`)。

### 10.4 无界延迟列表

`hindsight_client.py:43-46`：

```python
self.retain_latency_ms: List[float] = []
self.recall_latency_ms: List[float] = []
```

`reset()` 方法（86 行）会在超过 1000 条时裁剪，但 `reset()` 在整个代码库中**从未被调用**。长时间运行后，这些列表持续增长，且 `snapshot()` 中的 P99 计算每次都做 `sorted()`（O(n log n)）。

### 10.5 建议

- `recall_formatted()` 接收已获取的 items 作为参数，避免二次 HTTP 请求
- 将 `recall_memories()` 移到 while 循环外部
- 创建模块级共享的 `httpx.AsyncClient`
- 为延迟列表添加上限，或使用 t-digest 近似算法

---

## 十一、ContextBuilder 的重复磁盘 I/O ✅ 已清理

> **状态: 已完成。** `_is_wsl()` 和 `_is_docker()` 已添加 `@functools.lru_cache(maxsize=1)` 缓存。静态文件读取缓存仍待后续处理。

### 11.1 每轮读取静态文件

`harness/context_builder.py` 在每次 `build()` 调用时（即每轮 LLM 调用时）重新从磁盘读取：

| 文件 | 方法 | 行号 |
|------|------|------|
| `.hermes.md` | `_load_hermes_md()` | 128-154 |
| `AGENTS.md` / `CLAUDE.md` | `_load_context_file()` | 157-167 |
| `.cursorrules` + `*.mdc` | `_load_cursorrules()` | 170-188 |
| `SOUL.md` | `_load_soul_md()` | 191-201 |

这些文件在进程生命周期中不会变化。在 20 轮 tool turn 的请求中，会读取和解析这些文件 20 次。

### 11.2 每轮检测运行环境

`context_builder.py:60/67`：

```python
def _is_wsl() -> bool:
    with open("/proc/version", "r") as f:    # 每轮读磁盘
        content = f.read().lower()
        ...

def _is_docker() -> bool:
    return os.path.exists("/.dockerenv")      # 每轮 stat
```

这些检测结果在进程生命周期中是常量。应在模块加载时计算一次并缓存。

### 11.3 建议

- 对文件内容使用 `functools.lru_cache` 缓存
- 环境检测结果改为模块级常量
- 或在 `HarnessContextBuilder.__init__` 中预加载

---

## 十二、ToolRegistry 的重复计算

### 12.1 Freeze 后的重复构建

`tools/registry.py:66-92`，`freeze()` 后工具列表不再变化，但：

```python
def list_all(self) -> List[BaseTool]:
    with self._lock:
        return (
            list(self._native_tools.values())
            + list(self._mcp_tools.values())
            + list(self._skills.values())
        )
```

每次调用都重新从三个 dict 拼接新列表。`list_for_llm()` 在此基础上还要逐个遍历做 dict 转换。

### 12.2 建议

- `freeze()` 时将 `list_all()` 和 `list_for_llm()` 的结果缓存为实例属性
- `freeze()` 后的查询方法直接返回缓存

---

## 十三、未被使用的功能模块 ✅ 已清理

> **状态: 已完成。** 工具 RAG 已默认启用（`enabled: true`），`nsjail.py` 中 `_persist_result()` 和 `retrieve_result()` 已删除，`tools/executor.py:BaseToolExecutor` 已删除。

### 13.1 工具 RAG 检索

`config/models.yaml`：

```yaml
tool_rag:
  enabled: false    # 默认关闭
```

但 `tools/retriever.py`（~130 行）、`tools/store.py`（~110 行）、`resources/embedding.py`（新建）在启动时仍被 `setup_tools()` 尝试加载。虽然 `enabled: false` 守住了初始化逻辑，但模块导入和配置解析仍然执行。

### 13.2 孤立的 Redis 工具结果

`sandbox/nsjail.py:276-278`，每个工具执行结果写入 Redis：

```python
await self._redis_cache.set_json(
    f"sandbox:result:{execution_id}", result_data, ttl=3600
)
```

对应的 `retrieve_result()` 方法（359 行）在整个代码库中**零调用方**。所有写入的数据无人读取，浪费 Redis 存储和网络带宽。

### 13.3 `BaseToolExecutor` 协议

`tools/executor.py`（~47 行）定义了 `BaseToolExecutor` Protocol 加一个 `InProcessExecutor` 实现。Protocol 注释中提到 `NsjailExecutor` 和 `DockerExecutor`，但这两个实现**不存在于代码库中**。`InProcessExecutor.execute()` 是一个 3 行的 `registry.execute()` 透传，没有增加任何价值。

### 13.4 建议

- 禁用功能无需在启动时执行配置提取和判空逻辑，用早期 return 跳过
- 删除 `sandbox/nsjail.py` 中的 Redis 结果写入 + `retrieve_result()`
- `BaseToolExecutor` 直接删除，将 `InProcessExecutor` 的逻辑内联到 `ToolRegistry.execute()`

---

## 十四、数据目录与部署配置混乱

### 14.1 嵌套数据路径

`docker-compose.yaml` 中的卷挂载：

```yaml
- ./.data/data/workspace:/app/.data/data/workspace
- ./.data/data/sessions:/app/.data/data/sessions
- ./.data/data/logs:/app/.data/data/logs
- ./.data/data/napcat/data:/app/.napcat
- ./.data/data/napcat/cache:/app/data/cache
```

`.data/data/` 三层嵌套名难以理解其意图。同时 `.gitignore` 中同时存在 `data/` 和 `.data/` 两个忽略规则。

### 14.2 建议

- 统一为一个数据根目录（如 `.data/` 或 `data/`）
- 扁平化为 `.data/workspace/` / `.data/sessions/` / `.data/logs/` 等

---

## 十五、汇总

### 已删除的代码（~700 行）✅ 全部完成

| 模块 | 行数 | 状态 |
|------|------|------|
| `src/sandbox/tools/` 整个目录 | ~530 | ✅ 已删除 |
| `common/interfaces.py:ITool` | ~30 | ✅ 已删除 |
| `storage/protocols.py:PubSub` | ~40 | ✅ 已删除 |
| `storage/protocols.py` 多余错误码 | ~15 | ✅ 已精简 |
| `harness/runner.py:_resolve_tags_match()` | ~14 | ✅ 已删除 |
| `sandbox/nsjail.py:_persist_result + retrieve_result` | ~40 | ✅ 已删除 |
| `tools/executor.py:BaseToolExecutor` | ~20 | ✅ 已删除 |
| `worker.py` hasattr 模式 | ~20 | ✅ 已简化 |

### 应重构的代码（~500 行）

| 模块 | 行数 | 问题 |
|------|------|------|
| `sandbox/channels/napcat.py:normalize_message()` | ~160 | 163 行巨型级联 |
| `orchestration/worker.py:init_dependencies()` | ~164 | 上帝函数 |
| `orchestration/worker.py:setup_tools()` | ~97 | 4 个关注点混在一起 |
| `orchestration/worker.py:setup_*_schedule()` | ~80 | 两个函数复制粘贴 |
| `session/store.py` CRUD 方法 | ~80 | 7 次相同的 try/except/fallback 模板 |
| `harness/runner.py:recall_memories()` 参数链 | ~30 | 10 个参数，两条路径完全重复 |

### 应优化的性能问题

| 问题 | 预期收益 |
|------|---------|
| 消除双重 Hindsight recall 调用 | 每轮减少 1 次 HTTP 请求 |
| recall 移到 while 循环外 | 1 次替代最多 20 次召回 |
| httpx 连接池复用 | 消除每轮 TCP+TLS 握手 |
| ContextBuilder 文件内容缓存 | 消除每轮重复磁盘 I/O |
| ToolRegistry freeze 后缓存 | 消除每轮列表拼接+转换 |
| 串行工具执行改为并行 | 独立工具调用时间从 Σ→max |

### 总体评估

排除 `src/agent/` 后，约 **15-20%** 的代码属于可清理的冗余、死代码或过度抽象。**P0 清理已完成**（双重工具体系、死接口、假逻辑、孤立 Redis 持久化、BaseToolExecutor、配置 hasattr 模式），实际删除约 **700 行代码**。剩余重构项（NapCat 级联、HarnessRunner 长方法、性能优化）可依需后续处理。
