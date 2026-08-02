# HpAgent 工具系统架构

> 版本: v11 | 日期: 2026-06-04

---

## 目录

1. [架构概览](#1-架构概览)
2. [核心组件](#2-核心组件)
   - [2.1 ToolResult — 统一返回值](#21-toolresult--统一返回值)
   - [2.2 ToolRegistry — 三槽位注册中心](#22-toolregistry--三槽位注册中心)
   - [2.3 ToolFileStore — JSON 定义文件管理](#23-toolfilestore--json-定义文件管理)
3. [本地工具 (Native)](#3-本地工具-native)
   - [3.1 Bash](#31-bash)
   - [3.2 fs_read](#32-fs_read)
   - [3.3 fs_write](#33-fs_write)
   - [3.4 fs_edit](#34-fs_edit)
   - [3.5 Glob](#35-glob)
   - [3.6 Grep](#36-grep)
   - [3.7 路径安全](#37-路径安全)
4. [MCP 远程工具](#4-mcp-远程工具)
   - [4.1 协议与传输](#41-协议与传输)
   - [4.2 MCPSession — Streamable HTTP 客户端](#42-mcpsession--streamable-http-客户端)
   - [4.3 MCPToolManager — 多 Server 管理](#43-mcptoolmanager--多-server-管理)
   - [4.4 配置格式](#44-配置格式)
   - [4.5 会话保活与容错](#45-会话保活与容错)
5. [Skills 复合工具](#5-skills-复合工具)
   - [5.1 SkillPipeline — 流水线编排](#51-skillpipeline--流水线编排)
   - [5.2 步骤引用与参数模板](#52-步骤引用与参数模板)
   - [5.3 YAML 定义格式](#53-yaml-定义格式)
6. [RAG 工具检索](#6-rag-工具检索)
   - [6.1 ToolVectorStore — ChromaDB 向量存储](#61-toolvectorstore--chromadb-向量存储)
   - [6.2 ToolRetriever — 语义检索 + 精排](#62-toolretriever--语义检索--精排)
   - [6.3 索引流程](#63-索引流程)
7. [工具执行流程](#7-工具执行流程)
   - [7.1 启动加载](#71-启动加载)
   - [7.2 Per-Session 沙箱创建](#72-per-session-沙箱创建)
   - [7.3 Agentic Loop 中的工具调用](#73-agentic-loop-中的工具调用)
   - [7.4 结果截断与去重](#74-结果截断与去重)
8. [Nsjail 沙箱隔离](#8-nsjail-沙箱隔离)
9. [配置文件索引](#9-配置文件索引)
10. [扩展指南](#10-扩展指南)

---

## 1. 架构概览

HpAgent 的工具系统采用 **三槽位 + RAG 动态检索** 架构，基于 LangChain `BaseTool` 生态构建：

```
┌──────────────────────────────────────────────────────┐
│                  HarnessRunner                       │
│  (无状态协调器: 获取工具 → 调用模型 → 执行工具)        │
└──────────┬──────────────────────────────┬────────────┘
           │ _get_tools()                 │ _execute_tool()
           ▼                              ▼
┌──────────────────────────────────────────────────────┐
│                 ToolRegistry                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │  native  │  │   mcp    │  │  skill   │           │
│  │  槽位    │  │  槽位    │  │  槽位    │           │
│  └──────────┘  └──────────┘  └──────────┘           │
│                                                      │
│  ┌──────────────────────────────────────────────┐    │
│  │  ToolRetriever (RAG 语义检索 + Reranker 精排) │    │
│  │  └─ ToolVectorStore (ChromaDB 持久化)         │    │
│  └──────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
           │                    │
    ┌──────┴──────┐      ┌─────┴──────┐
    │ Local Tools │      │ MCP Remote │
    │ (进程内执行) │      │ (HTTP 调用) │
    └─────────────┘      └────────────┘
```

**设计原则:**

- **本地工具** 在 workspace 绑定的环境中 **进程内执行**，`fs_read`/`fs_write`/`fs_edit`/`Glob`/`Grep` 直接操作文件系统
- **Bash 工具** 可选通过 `nsjail` 加固（OS 级隔离），其他本地工具始终进程内
- **MCP 工具** 通过 Streamable HTTP 远端调用，启动时连接并缓存工具列表
- **Skill 工具** 是多个工具的流水线编排，对 LLM 暴露为单个工具
- **RAG 检索** 支持语义匹配动态注入工具，可选 Reranker 精排

---

## 2. 核心组件

### 2.1 ToolResult — 统一返回值

**文件:** `src/sandbox/tools/types.py`

所有工具执行返回统一的 `ToolResult` 结构，携带结构化元数据：

```python
@dataclass
class ToolResult:
    success: bool = True        # 执行是否成功
    output: Any = None          # 工具输出
    error: Optional[str] = None # 错误信息
    suggestion: Optional[str] = None  # 修复建议
    metadata: dict              # 额外元数据（如 execution_id、server、tool 等）
```

**转换规则：**
- LangChain `BaseTool.ainvoke()` 返回 `str` / `ToolMessage` 时，自动包装为 `ToolResult(success=True, output=...)`
- MCP 工具调用返回的 `content` 数组按 `type` 分别提取（`text` / `resource`）

### 2.2 ToolRegistry — 三槽位注册中心

**文件:** `src/sandbox/tools/registry.py`

核心数据结构，管理三种工具槽位：

| 槽位 | 存储 | 类别标记 | 说明 |
|------|------|---------|------|
| `_native_tools` | `Dict[str, BaseTool]` | `"native"` | 本地 Python 工具 |
| `_mcp_tools` | `Dict[str, BaseTool]` | `"mcp"` | MCP 协议远程工具 |
| `_skills` | `Dict[str, BaseTool]` | `"skill"` | Skills 复合工具 |

**关键方法:**

| 方法 | 说明 |
|------|------|
| `register(tool, category)` | 注册工具到指定槽位（freeze 前） |
| `freeze()` | 冻结注册表，禁止后续注册 |
| `get(name)` | 按名称查找（遍历三个槽位） |
| `get_category(name)` | 返回工具类别：`"native"` / `"mcp"` / `"skill"` |
| `list_all()` | 返回所有 `BaseTool` 实例列表 |
| `list_for_llm()` | 返回 OpenAI function calling 格式列表 |
| `retrieve_for_query(query, top_k)` | RAG 语义检索工具实例 |
| `retrieve_for_llm(query, top_k)` | RAG 语义检索 + 直接返回 LLM 格式 |
| `execute(tool_name, arguments)` | 按名称查找并执行工具 |

**线程安全:** 所有写操作由 `RLock` 保护。

### 2.3 ToolFileStore — JSON 定义文件管理

**文件:** `src/sandbox/tools/store.py`

从 JSON 文件加载工具元数据，动态构建 `StructuredTool`：

```python
store = ToolFileStore(base_path="tools/definitions")
defs = store.list_definitions("native")
tool = store.build_tool_from_definition(defs[0], my_execute_func)
```

**目录结构:**
```
tools/definitions/
├── native/
│   ├── calculator.json
│   ├── file_read.json
│   └── web_search.json
└── custom/           # 用户自定义工具
```

**JSON 定义格式:**
```json
{
  "name": "calculator",
  "description": "Evaluate a mathematical expression...",
  "category": "native",
  "parameters": {
    "type": "object",
    "properties": {
      "expression": {
        "type": "string",
        "description": "Mathematical expression to evaluate"
      }
    },
    "required": ["expression"]
  },
  "metadata": {
    "version": "1.0.0",
    "tags": ["math"]
  }
}
```

`build_tool_from_definition()` 从 JSON 参数定义动态创建 Pydantic `ArgsModel`，并关联执行协程，最终返回 `StructuredTool`。

---

## 3. 本地工具 (Native)

**目录:** `src/sandbox/tools/local/`

所有本地工具通过 **工厂函数** 创建，绑定特定的 `workspace_root` 路径。工厂映射定义在 `__init__.py`：

```python
LOCAL_TOOL_FACTORIES = {
    "fs_read":  create_fs_read_tool,
    "fs_write": create_fs_write_tool,
    "fs_edit":  create_fs_edit_tool,
    "Glob":     create_glob_tool,
    "Grep":     create_grep_tool,
    "Bash":     create_bash_tool,
}
```

### 3.1 Bash

**文件:** `src/sandbox/tools/local/bash.py`

- **名称:** `Bash`
- **参数:** `cmd` (必填), `cwd` (可选，相对 workspace), `timeout` (默认 120s, 最大 300s)
- **安全策略:**
  - 危险 `rm` 模式被正则阻断（如 `rm -rf`、`rm /*`）
  - 路径限制在 workspace 内（`safe_cwd` 路径遍历保护）
  - 输出截断至 50KB
- **执行方式:** `asyncio.create_subprocess_exec("bash", "-c", cmd)` 进程内执行
- **可选加固:** 当 `NsjailExecutor` 注入后，Bash 命令通过 `nsjail` 子进程执行
- **返回:** `{"stdout": ..., "stderr": ..., "exit_code": ...}`

### 3.2 fs_read

**文件:** `src/sandbox/tools/local/fs_read.py`

- **名称:** `fs_read`
- **参数:** `path` (文件路径), `offset` (1-indexed 起始行), `limit` (最大行数)
- **行为:**
  - 目录路径会提示使用 `Glob`
  - 文件超过 2000 行且未指定 limit 时，仅返回前 500 行并提示
  - 每行格式: `lineno\tcontent`
- **编码:** UTF-8（`errors="replace"`）

### 3.3 fs_write

**文件:** `src/sandbox/tools/local/fs_write.py`

- **名称:** `fs_write`
- **参数:** `path`, `content`（完整文件内容）
- **行为:** 创建或完全覆盖文件，自动创建中间目录
- **注意:** 需用 `fs_edit` 做定向修改

### 3.4 fs_edit

**文件:** `src/sandbox/tools/local/fs_edit.py`

- **名称:** `fs_edit`
- **参数:** `path`, `old_string`, `new_string`
- **精确字符串替换:**
  - `old_string` 必须唯一匹配（一次），否则返回错误并提示行号
  - `old_string=""` 则追加到文件末尾
  - 匹配 0 次 → 提示用 `fs_read` 查看当前内容
  - 匹配 >1 次 → 提示添加更多上下文使其唯一

### 3.5 Glob

**文件:** `src/sandbox/tools/local/glob_.py`

- **名称:** `Glob`
- **参数:** `root` (目录), `pattern` (glob 模式，支持 `**/*.py` 递归)
- **行为:** 按修改时间降序排列，最多 200 条，超量时标记 `truncated: true`
- **返回:** `{"matches": [{"path": ..., "size": ...}], ...}`

### 3.6 Grep

**文件:** `src/sandbox/tools/local/grep.py`

- **名称:** `Grep`
- **参数:** `root`, `query`, `glob` (文件过滤), `regex` (是否启用正则)
- **行为:** 默认字面量匹配，`regex=True` 时按正则搜索，最多 50 条
- **返回:** `{"matches": [{"path", "line", "col", "content"}, ...]}`

### 3.7 路径安全

**文件:** `src/sandbox/tools/local/_path_utils.py`

两个核心函数保证所有文件操作限定在 workspace 内：

| 函数 | 用途 | 行为 |
|------|------|------|
| `safe_resolve(workspace_root, user_path)` | 文件路径解析 | `os.path.normpath` 后检查前缀，拒绝 `../` 遍历 |
| `safe_cwd(workspace_root, cwd)` | Bash 工作目录解析 | 同上，空 cwd 时返回 workspace_root |
| `make_relative(workspace_root, abs_path)` | 绝对→相对路径 | `os.path.relpath` |

---

## 4. MCP 远程工具

**文件:** `src/sandbox/tools/adapters/mcp.py`

### 4.1 协议与传输

采用 **MCP (Model Context Protocol) Streamable HTTP** 传输模式：

- **单 HTTP 端点:** POST 发送 JSON-RPC 请求，响应体直接返回 JSON-RPC 结果
- **对比 SSE 模式:** 无需 GET 建立流、无需后台 reader、无需 endpoint 协商
- **协议版本:** `2024-11-05`

### 4.2 MCPSession — Streamable HTTP 客户端

每个 MCP Server 对应一个 `MCPSession`，管理 HTTP 连接与 JSON-RPC 通信：

```python
session = MCPSession(
    name="fetch",
    url="https://mcp.api-inference.modelscope.net/xxx/mcp",
    headers={"Authorization": "Bearer ${TOKEN}"},
    timeout=60.0,
    session_ttl=1500,  # 会话 TTL 默认 50 分钟（留 10 分钟安全边际）
)
await session.connect()        # 初始化握手 → 捕获 session ID → initialized
tools = await session.list_tools()  # tools/list
result = await session.call_tool("fetch", {"url": "..."})
await session.disconnect()
```

**MCP 握手流程:**
1. `initialize` → 获取 `serverInfo`、`protocolVersion`
2. 捕获响应头中的 `mcp-session-id`
3. 发送 `notifications/initialized`
4. 设置 `_connected = True`

**请求序列化:** `asyncio.Lock` 保证所有 POST 请求串行化。

**SSE 兼容:** 自动检测 `content-type: text/event-stream` 响应并解析 `data:` 行。

### 4.3 MCPToolManager — 多 Server 管理

管理所有 MCP Server 的完整生命周期：

```python
mgr = MCPToolManager("config/mcp/servers.yaml")
await mgr.load_config()   # 加载 YAML，替换 ${ENV_VAR}
await mgr.connect()       # 并发连接所有 server，缓存工具列表
tools = mgr.get_cached_tools()  # → List[StructuredTool]
await mgr.disconnect()
```

**初始化流程:**
1. 加载 YAML 配置，展开 `${ENV_VAR}` 占位符
2. 跳过 `disabled: true` 的 server
3. 并发连接每个 server → `list_tools()` → 构建 `StructuredTool`
4. 缓存工具名称到 `_truncation_map`（截断配置）
5. 日志汇总: `MCP connected: N/M servers, X tools total`

**工具构建:** `_build_langchain_tool()` 从 MCP `inputSchema` 动态创建 Pydantic `ArgsModel`，绑定 `_call_remote` 协程（内部调 `session.call_tool()`）。

**截断映射 `get_truncation_map()`:** 返回 `{tool_name: truncate_limit}` 字典，其中 `None` 表示不截断，`int` 表示截断字符数。非 MCP 工具的 tool_name 不在该字典中。

### 4.4 配置格式

**文件:** `config/mcp/servers.yaml`

```yaml
servers:
  tavily-mcp:
    url: "https://mcp.api-inference.modelscope.net/xxx/mcp"
    description: "tavily-websearch-mcp-server"
    # 可选字段:
    # headers:           自定义 HTTP 头，支持 ${ENV_VAR}
    # request_timeout:   请求超时秒数 (默认 60)
    # truncate_limit:    工具输出截断字符数 (缺省=不截断)
    # disabled: true     跳过此 server

  china-stock-mcp:
    url: "https://mcp.api-inference.modelscope.net/yyy/mcp"
    description: "china-stock-mcp-server"
```

### 4.5 会话保活与容错

**主动续期:** 每次 `_send_request()` 前调用 `_ensure_alive()`，检测 session 年龄是否超过 TTL（默认 1500s），超时则主动断开并重连。避免首次工具调用时才发现过期而产生的 3s 重连尾延迟。

**过期自动重连:** POST 返回 401/403 时，自动重新握手并重试一次请求。

**连接失败降级:** 单个 Server 连接失败不影响其他 Server，仅记录 warning 日志。

---

## 5. Skills 复合工具

**文件:** `src/sandbox/tools/skills/engine.py`

### 5.1 SkillPipeline — 流水线编排

将多个工具按顺序编排为一个高级能力。对 LLM 暴露为单个工具调用，内部由流水线步骤顺序执行：

```python
pipeline = SkillPipeline(
    name="daily_report",
    description="Generate a daily report from git log and weather data",
    steps=[
        {"id": "git_log",    "tool": "Bash",    "arguments": {"cmd": "git log --since='1 day ago'"}},
        {"id": "weather",    "tool": "web_search", "arguments": {"query": "$location weather today"}},
        {"id": "write_md",   "tool": "fs_write",   "arguments": {"path": "$date-report.md", "content": "$git_log\n\n$weather"}},
    ],
    on_error="stop",         # "stop" = 遇错立即中断；"continue" = 继续后续步骤
    timeout_seconds=60.0,
)

tool = build_skill_tool(pipeline, registry)
registry.register(tool, category="skill")
```

### 5.2 步骤引用与参数模板

- **`$step_id`** — 引用前序步骤的输出：`"content": "$git_log\n\n$weather"`
- **`$param`** — 引用 Skill 输入参数：`"query": "$location weather today"`
- **`$date`** — 内置上下文变量（在 `execute()` 中传入）

**错误策略:**
- `"stop"` (默认): 任一步骤失败立即返回错误，携带 `step_id` 和 `step_outputs`
- `"continue"`: 失败步骤输出为 error 字符串，继续后续步骤

### 5.3 YAML 定义格式

Skills 从 `tools/skills/` 目录加载 YAML 文件（当前为空，待定义）：

```yaml
name: daily_report
description: "Generate a daily report from git log and web search results"
on_error: stop
timeout_seconds: 60.0
pipeline:
  steps:
    - id: git_log
      tool: Bash
      arguments:
        cmd: "git log --oneline --since='1 day ago'"
    - id: search
      tool: web_search
      arguments:
        query: "$topic latest news"
    - id: write_report
      tool: fs_write
      arguments:
        path: "$date-report.md"
        content: "## Daily Report\n\n$git_log\n\n$search"
```

**加载逻辑** (in `worker.py:setup_tools()`):
```python
if config.models.skills.enabled:
    for skill_file in skills_path.glob("*.yaml"):
        skill_def = yaml.safe_load(skill_file.read_text())
        skill_definitions.append(skill_def)
```

---

## 6. RAG 工具检索

**文件:** `src/sandbox/tools/retriever.py`

### 6.1 ToolVectorStore — ChromaDB 向量存储

基于 ChromaDB 本地持久化的工具向量索引：

```python
store = ToolVectorStore(persist_path="tools/vectors")
store.index_tools(tools, embedding_client)  # 批量索引
store.sync(tools, embedding_client)         # 增量同步（删旧 + 增新）
```

**向量化文档格式:** `"{name}: {description} {param1}: {desc1} {param2}: {desc2}..."`

**ChromaDB 配置:**
- 集合名: `tool_definitions`
- 距离度量: `cosine`
- 元数据: `tool_name`, `category`

**增量同步策略 (`sync`):**
1. 获取现有 IDs 集合
2. 计算 `to_delete = existing_ids - current_ids`
3. 计算 `to_add = [t for t in tools if t.name not in existing_ids]`
4. 删除 + 新增（已有工具不重复索引）

### 6.2 ToolRetriever — 语义检索 + 精排

```python
retriever = ToolRetriever(vector_store, embedding_client, reranker_client=reranker)
tools = await retriever.retrieve("search the web for news", top_k=5, registry=registry)
llm_tools = await retriever.retrieve_for_llm(query, registry=registry, top_k=8)
```

**检索流程 (两阶段):**

1. **粗排 (ChromaDB):** 将用户查询向量化 → `collection.query(n_results=top_k*2)` → 候选集
2. **精排 (Reranker):** 可选。当 `reranker_client` 存在且候选数 > top_k 时，调 `reranker.rerank(query, documents, top_n=top_k)` 精排

**降级策略:**
- `retriever=None` 时，`retrieve_for_query()` 返回全部工具 (`list_all()`)
- Reranker 不可用时，ChromaDB 直接召回 top_k 返回

### 6.3 索引流程

**触发时机:** 每个 session 首次创建沙箱时 (`SandboxManager.create_session_sandbox()`)

```python
# 首次创建沙箱时同步工具向量库（增量，后续 session 跳过已有工具）
if self._retriever is not None:
    self._retriever._store.sync(
        registry.list_all(),
        embedding_client=self._retriever._embedding,
    )
```

**持久化路径:** `tools/vectors/` (ChromaDB 数据文件)

---

## 7. 工具执行流程

### 7.1 启动加载

**文件:** `src/orchestration/worker.py → setup_tools()`

```
AppConfig.from_yaml()
    │
    ▼
setup_tools(config)
    ├── 1. RAG 检索器 (可选, tool_rag.enabled)
    │   ├── EmbeddingClient (SiliconFlow BGE-M3)
    │   ├── RerankerClient (可选, BGE-Reranker-v2-m3)
    │   ├── ToolVectorStore (ChromaDB 持久化)
    │   └── → ToolRetriever
    │
    ├── 2. MCP 工具 (可选, mcp.auto_connect)
    │   ├── MCPToolManager.load_config()
    │   └── MCPToolManager.connect()
    │       ├── 连接每个 server
    │       ├── list_tools() → CachedTool
    │       └── _build_langchain_tool() → StructuredTool
    │       └── → List[StructuredTool] (共享缓存的工具列表)
    │
    └── 3. Skills (可选, skills.enabled)
        └── 扫描 tools/skills/*.yaml
            └── → List[dict] (skill_definitions)
```

**返回值:** `(mcp_manager, skill_definitions, retriever)` → 全部注入 `SandboxManager`

### 7.2 Per-Session 沙箱创建

**文件:** `src/sandbox/sandbox_manager.py → create_session_sandbox()`

每个会话创建一个独立的 `Sandbox`，绑定专属的 `ToolRegistry`：

```
create_session_sandbox(session_id, workspace_path)
    │
    ├── 1. 创建 ToolRegistry(retriever=共享的 RAG 检索器)
    │
    ├── 2. 注册本地工具 (工厂函数 + workspace_path)
    │   └── for name, factory in LOCAL_TOOL_FACTORIES.items():
    │       tool = factory(workspace_path)  → registry.register(tool, "native")
    │
    ├── 3. 注册 MCP 工具 (共享缓存)
    │   └── for tool in mcp_manager.get_cached_tools():
    │       registry.register(tool, "mcp")
    │
    ├── 4. 注册 Skills (共享定义，新建 pipeline 实例)
    │   └── for skill_def in skill_definitions:
    │       pipeline = SkillPipeline(name, desc, steps, on_error, timeout)
    │       tool = build_skill_tool(pipeline, registry)
    │       registry.register(tool, "skill")
    │
    ├── 5. registry.freeze()  ← 冻结后不可再注册
    │
    ├── 6. 增量同步工具向量 (仅首次新工具)
    │
    └── 7. 创建 Sandbox(workspace_path, registry)
```

### 7.3 Agentic Loop 中的工具调用

**文件:** `src/harness/runner.py`

```
HarnessRunner.process_turn()
    │
    ├── _get_tools(user_content, session_id)
    │   ├── RAG 启用: sandbox._registry.retrieve_for_llm(query, top_k=8)
    │   │   └── 返回 Top-K 相关工具的 OpenAI function calling 格式
    │   └── RAG 禁用: sandbox.list_tools()
    │       └── 返回全部工具的 OpenAI function calling 格式
    │
    └── _execute_tool(tool_name, arguments, session_id)
        ├── 去重检查 (web_search / web_fetch / tavily_search 缓存)
        ├── sandbox.execute(tool_name, arguments)
        │   ├── native + nsjail 启用 → nsjail.execute()
        │   ├── native 否则 → tool.ainvoke() 进程内执行
        │   ├── mcp → tool.ainvoke() → session.call_tool()
        │   └── skill → tool.ainvoke() → pipeline.execute()
        │
        └── _apply_truncation(result_dict, output, tool_name)
            ├── MCP 工具: 使用 servers.yaml 的 truncate_limit
            └── 本地工具: 使用 AgentConfig 的 tool_result_max_chars/tool_result_truncated
```

### 7.4 结果截断与去重

**截断 (`_apply_truncation`):**

| 工具类型 | 截断配置来源 | 超出后行为 |
|----------|-------------|-----------|
| MCP 工具 | `servers.yaml` 的 `truncate_limit` (None=不截断, int=保留字符数) | 截断 + 存储完整输出至 `file_store` |
| 本地工具 | `AgentConfig.tool_result_max_chars` (超限阈值), `tool_result_truncated` (保留字符数) | 截断至 `truncated` 长度 + 存储完整输出 |

**去重 (`_execute_tool`):**
- 仅对搜索类工具生效: `web_search`, `web_fetch`, `tavily_search`
- 缓存键: `"{tool_name}:{json.dumps(arguments)}"`
- 每个工具名保留最近 3 条缓存，避免重复 API 调用

---

## 8. Nsjail 沙箱隔离

**文件:** `src/sandbox/nsjail.py`

可选的 OS 级隔离层，**仅对 Bash 工具加固**。其他本地工具（`fs_read`/`fs_write`/`fs_edit` 等）始终进程内执行。

**配置:** `SandboxConfig` → `NsjailConfig`

```python
@dataclass
class NsjailConfig:
    nsjail_binary: str = "/usr/bin/nsjail"
    chroot_path: str = "/"
    time_limit: int = 30           # 命令超时 (秒)
    memory_limit_mb: int = 256     # 内存限制
    cpu_limit_seconds: int = 10    # CPU 时间限制
    max_processes: int = 32        # 最大进程数
    max_files: int = 64            # 最大文件句柄
    disable_proc: bool = True      # 禁用 /proc
    disable_network: bool = True   # 禁用网络
    readonly_root: bool = True     # 根文件系统只读
    user: str = "nobody"
    group: str = "nogroup"
```

**命令构建 (`NsjailConfig.build_command`):**
```
nsjail --mode o --chroot / --cwd /work --user nobody --group nogroup
       --time_limit 30 --rlimit_as 256 --rlimit_cpu 10 --rlimit_nofile 64
       --rlimit_nproc 32 --disable_proc --iface_no_lo --really_quiet
       -- /bin/bash -c '<command>'
```

**降级:** nsjail 二进制不存在时，Bash 降级为进程内执行（记录 warning 日志）。

---

## 9. 配置文件索引

| 文件 | 用途 | 加载位置 |
|------|------|---------|
| `config/models.yaml` | 模型提供者、降级链、工具 RAG/MCP/Skills 配置 | `ModelsConfig.from_yaml()` |
| `config/mcp/servers.yaml` | MCP Server 连接信息 | `MCPToolManager.load_config()` |
| `config/config.yaml` | 应用顶层配置 (Temporal/Redis/Sandbox/Agent) | `AppConfig.from_yaml()` |
| `config/prompts/*.yaml` | Agent prompt 模板 (identities/guidance/environment/system) | `PromptsConfig.from_dir()` |
| `config/agents.yaml` | 多Agent 模式 Agent 定义 | `AppConfig.from_yaml()` |
| `tools/definitions/native/*.json` | 本地工具 JSON 元数据定义 | `ToolFileStore.list_definitions()` |
| `tools/definitions/custom/*.json` | 用户自定义工具 JSON 定义 | `ToolFileStore.list_definitions()` |
| `tools/skills/*.yaml` | Skills 流水线定义 | `worker.py:setup_tools()` |
| `tools/vectors/` | ChromaDB 工具向量持久化 | `ToolVectorStore.__init__()` |

**关键配置项 (`models.yaml`):**

```yaml
tool_rag:
  enabled: true               # 是否启用 RAG 动态注入
  top_k: 8                    # 每轮注入工具数量
  persist_path: "tools/vectors"  # ChromaDB 路径

mcp:
  config_path: "config/mcp/servers.yaml"
  auto_connect: true           # 启动时自动连接 MCP Server

skills:
  config_path: "tools/skills/"
  enabled: true

rerank:
  provider: siliconflow
  model: "BAAI/bge-reranker-v2-m3"  # 精排模型
  timeout: 10.0
  top_n: 10
```

---

## 10. 扩展指南

### 添加新的本地工具

1. **创建工具文件** `src/sandbox/tools/local/my_tool.py`：

```python
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from ._path_utils import safe_resolve

class MyToolInput(BaseModel):
    arg1: str = Field(description="...")

def create_my_tool(workspace_root: str):
    async def my_tool(arg1: str) -> str:
        # 实现逻辑
        return "result"

    return StructuredTool.from_function(
        name="my_tool",
        description="...",
        args_schema=MyToolInput,
        coroutine=my_tool,
    )
```

2. **注册工厂** 在 `src/sandbox/tools/local/__init__.py` 的 `LOCAL_TOOL_FACTORIES` 中添加:

```python
LOCAL_TOOL_FACTORIES = {
    # ...
    "my_tool": create_my_tool,
}
```

### 添加新的 MCP Server

编辑 `config/mcp/servers.yaml`：

```yaml
servers:
  my-service:
    url: "https://my-mcp-server.example.com/mcp"
    headers:
      Authorization: "Bearer ${MY_API_KEY}"
    request_timeout: 30.0
    truncate_limit: 4000
    description: "My custom MCP service"
```

### 添加新的 Skill

创建 `tools/skills/my_skill.yaml`：

```yaml
name: my_skill
description: "Description shown to LLM"
on_error: stop
timeout_seconds: 30.0
pipeline:
  steps:
    - id: step1
      tool: Bash
      arguments:
        cmd: "echo 'processing $input_data'"
    - id: step2
      tool: fs_write
      arguments:
        path: "output.txt"
        content: "$step1"
```
