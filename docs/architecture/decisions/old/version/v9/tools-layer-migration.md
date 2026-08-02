# Tools Layer 架构文档 v2

> 基于 LangChain 生态重构 HpAgent 工具体系 —— 核心工具 / MCP / Skills 的统一管理。

---

## 目录

1. [设计动机：为什么选择 LangChain](#设计动机为什么选择-langchain)
2. [总体架构](#总体架构)
3. [目录结构](#目录结构)
4. [核心抽象：LangChain BaseTool 适配](#核心抽象langchain-basetool-适配)
5. [工具注册中心：ToolRegistry](#工具注册中心toolregistry)
6. [RAG 动态工具注入](#rag-动态工具注入)
7. [本地文件存储](#本地文件存储)
8. [MCP 协议集成](#mcp-协议集成)
9. [Skills 复合工具编排](#skills-复合工具编排)
10. [执行器与沙箱](#执行器与沙箱)
11. [Agent Loop 集成](#agent-loop-集成)
12. [配置与启动流程](#配置与启动流程)
13. [迁移检查清单](#迁移检查清单)

---

## 设计动机：为什么选择 LangChain

### 当前问题

HpAgent 的工具体系从零构建了 `BaseTool` / `ToolRegistry` / `DynamicTool` / `ToolFactory`，这些组件与 LangChain 的 `BaseTool` / `StructuredTool` / `ToolExecutor` 高度同构，但缺失了生态集成能力。

### 选择 LangChain 的理由

| 能力 | 自建 | LangChain |
|------|:---:|:---:|
| 工具定义 (`@tool` / `StructuredTool`) | DynamicTool 手工拼装 | `@tool` 装饰器 + Pydantic 自动生成 JSON Schema |
| MCP 协议 | 需从零实现 MCP Client | `langchain-mcp-adapters` 开箱即用 |
| 工具组合编排 | 需自建 SkillTool | `AgentExecutor` + `RunnableSequence` 内置 |
| RAG 检索增强 | 需集成向量库 | `VectorStoreRetriever` + 内置 embedding 支持 |
| 流式回调 | 需自建 callback 链 | `Callbacks` / `astream_events` 原生支持 |
| 错误重试 | 需手动 try/except | `with_fallbacks()` / `RetryPolicy` |
| 社区生态 | 0 | MCP Server / 搜索 / 代码执行等数百现成工具 |

### 保留现有代码中的

- `ToolResult` — 统一返回值包装（LangChain 没有等价物，保留并作为 execute 返回类型）
- `common.types.ToolCall` — 与 HpAgent 事件系统耦合，保留
- `ToolRegistry.freeze()` — 注册阶段封禁，保留
- `NsjailExecutor` — OS 级隔离执行，保留并包装为 LangChain 兼容的 `BaseToolExecutor`

### 替换为 LangChain 的

- `BaseTool` ABC → `langchain_core.tools.BaseTool`（内置 Pydantic 校验 + 自动 schema 生成）
- `DynamicTool` → `StructuredTool.from_function()` 或 `@tool` 装饰器
- `ToolFactory` → 拆分为文件加载器 + RAG 检索器

---

## 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│                        Agent Loop (HarnessRunner)             │
│  process_turn() → recall → context → model → tools → retain  │
└──────────────────────┬───────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ list_tools()  │ execute()  │ RAG 检索  │
    │ → LLM 注入    │ → 沙箱执行 │ → 动态注入 │
    └──────┬───┘ └────┬─────┘ └────┬─────┘
           │           │            │
           ▼           ▼            ▼
┌──────────────────────────────────────────────────────────────┐
│                     ToolRegistry (注册中心)                    │
│                                                              │
│  内部索引:                                                    │
│    _native_tools: dict[str, BaseTool]       # 本地工具        │
│    _mcp_tools:    dict[str, BaseTool]       # MCP 工具        │
│    _skills:       dict[str, SkillPipeline]  # 复合技能        │
│                                                              │
│  RAG 层:                                                     │
│    ToolVectorStore (ChromaDB local)         # 工具向量索引    │
│    ToolRetriever                            # 语义检索器      │
└──────────┬───────────────────────────────────────────────────┘
           │
    ┌──────┼──────┬──────────────┐
    ▼      ▼      ▼              ▼
┌──────┐ ┌─────┐ ┌────────┐ ┌──────────────┐
│ 本地  │ │ MCP │ │ Skills │ │ RAG Retriever│
│ 文件  │ │Client│ │ 文件   │ │ (ChromaDB)   │
│ JSON │ │      │ │ YAML   │ │              │
└──────┘ └─────┘ └────────┘ └──────────────┘
```

**核心原则**：
- **大脑不碰世界**：工具执行仍经过沙箱代理（nsjail/进程内），LangChain 只管理元数据和调用链
- **RAG 按需注入**：不把所有工具一次性塞给 LLM，而是根据对话上下文语义检索 Top-N 相关工具
- **文件即真相**：工具定义存储在 JSON/YAML 文件中，代码加载 + 版本可控

---

## 目录结构

```
tools/
├── __init__.py              # 公共 API 导出
│                            #   ToolRegistry, create_default_registry,
│                            #   ToolVectorStore, ToolRetriever
│
├── types.py                 # HpAgent 自有类型（保留现有）
│                            #   ToolResult, ToolType(str, Enum)
│
├── registry.py              # 工具注册中心
│                            #   ToolRegistry: 三槽位(本地/MCP/Skill) + RAG 集成
│
├── store.py                 # 本地文件存储
│                            #   ToolFileStore: JSON/YAML 文件读写
│                            #   目录结构: definitions/  → 工具元数据
│                            #             skills/      → Skill 编排定义
│
├── retriever.py             # RAG 动态工具检索
│                            #   ToolVectorStore: ChromaDB 本地持久化
│                            #   ToolRetriever: 语义检索 + 过滤器
│
├── executor.py              # 执行器（保留 NsjailExecutor，新增 InProcessExecutor）
│                            #   BaseToolExecutor (Protocol)
│                            #   InProcessExecutor
│                            #   NsjailExecutor (保留现有)
│
├── adapters/                # 适配层 —— 连接 LangChain 与 HpAgent
│   ├── __init__.py
│   ├── langchain_adapter.py #   LangChain BaseTool → HpAgent ToolResult 适配
│   └── mcp.py               #   langchain-mcp-adapters → ToolRegistry 桥接
│
├── builtin/                 # 本地内置工具（基于 @tool 装饰器）
│   ├── __init__.py
│   ├── calculator.py        #   数学计算 — StructuredTool
│   ├── file_ops.py          #   文件读写 — 可配置 root_dir
│   └── web_search.py        #   网络搜索 — Tavily / Bing API
│
├── definitions/             # 工具定义文件（本地存储，版本可控）
│   ├── native/              #   本地工具元数据
│   │   ├── calculator.json
│   │   ├── file_read.json
│   │   └── web_search.json
│   ├── mcp/                 #   MCP 服务器连接配置
│   │   └── servers.yaml
│   └── custom/              #   用户自定义工具
│       └── .gitkeep
│
├── skills/                  # Skills 编排定义
│   ├── daily_report.yaml
│   └── code_review.yaml
│
└── vectors/                 # ChromaDB 向量持久化目录（gitignored）
    └── .gitkeep
```

### 依赖方向

```
types.py              ←  HpAgent 自有类型（ToolResult），零外部依赖
    ↑
registry.py           ←  ToolRegistry，依赖 LangChain BaseTool + types
    ↑
store.py              ←  文件存储，只依赖 JSON/YAML 解析
    ↑
retriever.py          ←  RAG 检索，依赖 ChromaDB + embedding 客户端
    ↑
adapters/             ←  适配 LangChain → HpAgent 类型转换
    ↑
builtin/              ←  内置工具实现，使用 @tool / StructuredTool
executor.py           ←  执行器，独立于 LangChain（可插拔隔离后端）
```

---

## 核心抽象：LangChain BaseTool 适配

### 工具定义方式

不再继承自定义 ABC，而是使用 LangChain 的 `StructuredTool`：

```python
# tools/builtin/calculator.py
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

class CalculatorInput(BaseModel):
    expression: str = Field(description="Mathematical expression to evaluate, e.g. '2+3*4'")

async def _calculate(expression: str) -> str:
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"Error: {e}"

calculator_tool = StructuredTool.from_function(
    name="calculator",
    description="Evaluate a mathematical expression. Supports +, -, *, /, **, %, and parentheses.",
    args_schema=CalculatorInput,
    coroutine=_calculate,
)
```

或使用 `@tool` 装饰器（更简洁）：

```python
from langchain_core.tools import tool

@tool
async def calculator(expression: str) -> str:
    """Evaluate a mathematical expression. Supports +, -, *, /, **, %, and parentheses.

    Args:
        expression: Mathematical expression to evaluate, e.g. '2+3*4'
    """
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"Error: {e}"
```

### HpAgent → LangChain 适配器

LangChain 的 `BaseTool` 内部返回值是 `str` 或 `ToolMessage`，而 HpAgent 的编排层期望 `ToolResult`。适配器完成这个转换：

```python
# tools/adapters/langchain_adapter.py
from typing import Any, Dict
from langchain_core.tools import BaseTool as LCBaseTool
from tools.types import ToolResult


def wrap_langchain_tool(lc_tool: LCBaseTool):
    """将 LangChain BaseTool 的执行包装为 HpAgent ToolResult。

    LangChain 原生工具执行流程:
      lc_tool.ainvoke(input) → str / ToolMessage

    HpAgent 期望:
      await execute(**kwargs) → ToolResult

    此适配器修改工具的 _run/_arun，使其返回 ToolResult。
    采用组合而非继承，避免侵入 LangChain 内部实现。
    """

    original_arun = lc_tool._arun

    async def _arun_wrapped(*args, **kwargs) -> ToolResult:
        try:
            output = await original_arun(*args, **kwargs)
            # LangChain 工具可能返回 str、ToolMessage 或 dict
            if hasattr(output, "content"):
                output = output.content
            return ToolResult(success=True, output=str(output))
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    lc_tool._arun = _arun_wrapped
    return lc_tool
```

### ToolResult 保留（不替换）

```python
# tools/types.py
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class ToolResult:
    """工具执行统一返回值 —— HpAgent 保留类型。

    LangChain 的 BaseTool 返回 str/ToolMessage，
    HpAgent 保留此类型以携带结构化元数据。
    """
    success: bool = True
    output: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
```

---

## 工具注册中心：ToolRegistry

`ToolRegistry` 管理三类工具：本地（Native）、MCP、Skill。基于 LangChain `BaseTool`，三槽位统一管理。

```python
# tools/registry.py
from typing import Dict, List, Optional
from threading import RLock
from langchain_core.tools import BaseTool

from tools.types import ToolResult
from tools.retriever import ToolRetriever


class ToolRegistry:
    """工具注册中心 —— 三槽位 + RAG 检索。

    三槽位:
      _native_tools: 本地 Python 工具
      _mcp_tools:    MCP 协议工具
      _skills:       Skills 复合工具

    RAG 层:
      _retriever: ToolRetriever 实例，支持语义检索动态注入
    """

    def __init__(self, retriever: Optional[ToolRetriever] = None):
        self._native_tools: Dict[str, BaseTool] = {}
        self._mcp_tools: Dict[str, BaseTool] = {}
        self._skills: Dict[str, BaseTool] = {}
        self._lock = RLock()
        self._frozen = False
        self._retriever = retriever

    # ── 注册 / 注销 ─────────────────────────────────────────────

    def register(self, tool: BaseTool, category: str = "native") -> None:
        if self._frozen:
            raise RuntimeError("ToolRegistry is frozen")
        with self._lock:
            target = {"native": self._native_tools,
                      "mcp": self._mcp_tools,
                      "skill": self._skills}[category]
            target[tool.name] = tool

    def unregister(self, name: str) -> bool:
        with self._lock:
            for d in (self._native_tools, self._mcp_tools, self._skills):
                if name in d:
                    del d[name]
                    return True
            return False

    def freeze(self) -> None:
        """封禁注册 —— 启动后禁止添加新工具，防止运行时注入。"""
        self._frozen = True

    # ── 查询 ────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[BaseTool]:
        with self._lock:
            for d in (self._native_tools, self._mcp_tools, self._skills):
                if name in d:
                    return d[name]
            return None

    def list_all(self) -> List[BaseTool]:
        with self._lock:
            return list(self._native_tools.values()) + \
                   list(self._mcp_tools.values()) + \
                   list(self._skills.values())

    # ── LLM 工具格式输出 ────────────────────────────────────────

    def list_for_llm(self) -> List[dict]:
        """所有工具的 OpenAI function calling 格式。

        LangChain BaseTool 内置 to_openai_function() / to_anthropic_tool()。
        """
        tools = self.list_all()
        return [t.to_openai_function() if hasattr(t, "to_openai_function")
                else {"type": "function", "function": {
                    "name": t.name, "description": t.description,
                    "parameters": t.args_schema.schema() if t.args_schema else {},
                }} for t in tools]

    # ── RAG 动态检索 ────────────────────────────────────────────

    async def retrieve_for_query(self, query: str, top_k: int = 5) -> List[BaseTool]:
        """基于用户查询语义检索 Top-K 相关工具。

        如果 RAG 层未初始化，回退到返回所有工具。
        """
        if self._retriever is None:
            return self.list_all()
        return await self._retriever.retrieve(query, top_k=top_k)

    # ── 执行 ────────────────────────────────────────────────────

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"Tool '{tool_name}' not found")
        try:
            result = await tool.ainvoke(arguments)
            if isinstance(result, ToolResult):
                return result
            output = result.content if hasattr(result, "content") else str(result)
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def clear(self) -> None:
        with self._lock:
            self._native_tools.clear()
            self._mcp_tools.clear()
            self._skills.clear()
```

---

## RAG 动态工具注入

### 问题

随着工具数量增长（数十个 MCP Server × 每个 Server 数十个工具），一次性将所有工具定义注入 LLM context 会：
- 消耗大量 token（每个工具定义约 200-500 tokens）
- 降低 LLM 工具选择准确率（选项越多越容易选错）
- 无法利用上下文信息做智能筛选

### 方案

将每个工具的 name + description + parameter descriptions 做 embedding 存入本地 ChromaDB。每轮对话前根据用户意图做语义检索，仅注入 Top-K 相关工具。

```
对话轮次开始
  │
  ▼
user_query = "帮我计算 3.14 * 2.5，然后搜索圆周率最新研究"
  │
  ▼
ToolRetriever.retrieve(query=user_query, top_k=5)
  │  向量相似度计算
  │  ChromaDB 本地持久化
  ▼
返回 Top-5 工具:
  1. calculator     (score: 0.92)  ← 命中"计算"
  2. web_search     (score: 0.88)  ← 命中"搜索"
  3. file_read      (score: 0.45)  ← 低相关
  4. python_repl    (score: 0.40)
  5. fetch_url      (score: 0.38)
  │
  ▼
仅注入这 5 个工具到 LLM tools 参数
```

### 实现

```python
# tools/retriever.py
from typing import List, Optional
import chromadb
from chromadb.config import Settings
from langchain_core.tools import BaseTool


class ToolVectorStore:
    """工具向量存储 —— 基于 ChromaDB 本地持久化。

    ChromaDB 数据目录: tools/vectors/
    每个工具的向量由 name + description 组成。
    """

    def __init__(self, persist_path: str = "tools/vectors"):
        self._client = chromadb.PersistentClient(
            path=persist_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name="tool_definitions",
            metadata={"hnsw:space": "cosine"},
        )

    def index_tools(self, tools: List[BaseTool], embedding_client) -> None:
        """将工具列表向量化并写入 ChromaDB。

        Args:
            tools: LangChain BaseTool 列表。
            embedding_client: 任意 embedding 客户端，需实现 embed_texts(texts) → List[List[float]]。
        """
        docs = []
        ids = []
        metadatas = []
        for tool in tools:
            doc = f"{tool.name}: {tool.description}"
            # 如果有参数描述也加入
            if tool.args_schema:
                for field_name, field_info in tool.args_schema.model_fields.items():
                    doc += f" {field_name}: {field_info.description or ''}"

            docs.append(doc)
            ids.append(tool.name)
            metadatas.append({
                "tool_name": tool.name,
                "category": getattr(tool, "category", "native"),
            })

        if docs:
            embeddings = embedding_client.embed_texts(docs)
            self._collection.upsert(
                ids=ids,
                documents=docs,
                embeddings=embeddings,
                metadatas=metadatas,
            )

    def sync(self, tools: List[BaseTool]) -> None:
        """增量同步 —— 删除 ChromaDB 中已移除的工具，新增未索引的工具。"""
        existing_ids = set(self._collection.get()["ids"])
        current_ids = {t.name for t in tools}

        to_delete = existing_ids - current_ids
        if to_delete:
            self._collection.delete(ids=list(to_delete))

        to_add = [t for t in tools if t.name not in existing_ids]
        if to_add:
            # 延迟导入避免循环引用
            from resources.embedding import embedding_client
            self.index_tools(to_add, embedding_client)


class ToolRetriever:
    """工具语义检索器 —— 基于用户查询检索 Top-K 相关工具。"""

    def __init__(self, vector_store: ToolVectorStore, embedding_client):
        self._store = vector_store
        self._embedding = embedding_client

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        category_filter: Optional[str] = None,
        registry=None,
    ) -> List[BaseTool]:
        """根据用户查询语义检索相关工具。

        Args:
            query: 用户查询文本或对话上下文摘要。
            top_k: 返回工具数量。
            category_filter: 可选，只检索指定类别（"native" / "mcp" / "skill"）。
            registry: ToolRegistry 实例，用于将名称解析为 BaseTool 对象。

        Returns:
            按语义相似度排序的 BaseTool 列表。
        """
        query_embedding = self._embedding.embed_texts([query])[0]

        where_filter = None
        if category_filter:
            where_filter = {"category": category_filter}

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter,
        )

        tool_names = results["ids"][0] if results["ids"] else []

        if registry is None:
            return []

        tools = []
        for name in tool_names:
            tool = registry.get(name)
            if tool:
                tools.append(tool)
        return tools

    async def retrieve_for_llm(
        self,
        query: str,
        registry,
        top_k: int = 5,
    ) -> List[dict]:
        """直接返回 LLM 可用的工具定义列表。"""
        tools = await self.retrieve(query, top_k=top_k, registry=registry)
        return [t.to_openai_function() if hasattr(t, "to_openai_function")
                else {"type": "function", "function": {
                    "name": t.name, "description": t.description,
                    "parameters": t.args_schema.schema() if t.args_schema else {},
                }} for t in tools]
```

### RAG Embedding 集成

工具向量化的 embedding 复用 HpAgent 现有的 embedding 客户端配置（`config/models.yaml` 中的 `embedding` 降级链）：

```python
# resources/embedding.py
"""工具 RAG 使用的 embedding 客户端，复用 models.yaml 中的 embedding 配置链。"""
from typing import List
from common.types import ModelResponse


class EmbeddingClient:
    """轻量级 embedding 客户端 —— 从 models.yaml embedding 配置创建。

    Usage:
        client = EmbeddingClient(provider_config, model_chain)
        vectors = client.embed_texts(["calculator: evaluate math expression"])
    """

    def __init__(self, config: dict):
        self._config = config
        # 与 ModelClient 共享 provider 凭证，但只做 embedding API 调用

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """将文本列表向量化。"""
        ...
```

---

## 本地文件存储

### 设计原则

- **文件即源码**：工具定义以 JSON/YAML 存储在 `tools/definitions/` 下，纳入版本控制
- **加载时校验**：启动时读取文件，通过 Pydantic 校验结构完整性
- **双向同步**：代码中的 `@tool` 装饰器生成运行时 BaseTool，同时可导出为 JSON 文件；JSON 文件也可反向加载为 DynamicTool

### 目录布局

```
tools/definitions/
├── native/
│   ├── calculator.json
│   ├── file_read.json
│   ├── file_write.json
│   └── web_search.json
├── mcp/
│   ├── servers.yaml           # MCP Server 连接配置
│   └── overrides/             # 可选：覆盖特定 MCP 工具的 description
│       └── filesystem.read.json
└── custom/                    # 用户自定义工具（.gitkeep 占位）
    └── .gitkeep
```

### 工具定义文件格式

```json
// tools/definitions/native/web_search.json
{
  "name": "web_search",
  "description": "Search the web for real-time information. Use when the user asks about current events or facts beyond your knowledge cutoff.",
  "category": "native",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Search query string"
      },
      "max_results": {
        "type": "integer",
        "default": 5,
        "description": "Maximum number of results to return"
      }
    },
    "required": ["query"]
  },
  "metadata": {
    "version": "1.0.0",
    "author": "HpAgent",
    "tags": ["search", "web", "internet"],
    "requires_api_key": true,
    "api_service": "tavily"
  }
}
```

### 文件存储实现

```python
# tools/store.py
import json
import os
from pathlib import Path
from typing import List, Dict, Optional
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import create_model


class ToolFileStore:
    """工具定义文件的本地存储管理器。

    职责:
      1. 从 JSON 文件加载工具元数据
      2. 将运行时工具实例导出为 JSON 文件
      3. 构建 StructuredTool 实例（元数据 + 执行函数链接）
    """

    def __init__(self, base_path: str = "tools/definitions"):
        self._base = Path(base_path)

    # ── 读取 ─────────────────────────────────────────────────────

    def list_definitions(self, category: Optional[str] = None) -> List[dict]:
        """列出所有工具定义文件的内容。

        Args:
            category: 过滤类别 ("native" / "mcp" / "custom")，None 表示全部。
        """
        definitions = []
        search_path = self._base / category if category else self._base
        for file_path in search_path.rglob("*.json"):
            data = json.loads(file_path.read_text(encoding="utf-8"))
            data["_source_path"] = str(file_path)
            definitions.append(data)
        return definitions

    def load_tool_definition(self, name: str, category: str = "native") -> Optional[dict]:
        """按名称加载单个工具定义。"""
        file_path = self._base / category / f"{name}.json"
        if not file_path.exists():
            return None
        return json.loads(file_path.read_text(encoding="utf-8"))

    # ── 写入 ─────────────────────────────────────────────────────

    def save_definition(self, definition: dict, category: str = "custom") -> str:
        """保存工具定义到文件。

        Args:
            definition: 工具定义字典。
            category: 保存类别。

        Returns:
            写入的文件路径。
        """
        target_dir = self._base / category
        target_dir.mkdir(parents=True, exist_ok=True)
        name = definition.get("name", "unnamed")
        file_path = target_dir / f"{name}.json"
        file_path.write_text(
            json.dumps(definition, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(file_path)

    # ── 构建工具 ─────────────────────────────────────────────────

    def build_tool_from_definition(
        self,
        definition: dict,
        execute_func,
    ) -> StructuredTool:
        """从 JSON 定义 + 执行函数构建 StructuredTool。

        这是连接"文件元数据"与"代码执行逻辑"的关键方法。

        Args:
            definition: 从 JSON 加载的工具定义字典。
            execute_func: async callable，签名为 async def func(**kwargs) → str。

        Returns:
            StructuredTool 实例。
        """
        # 从 parameters JSON Schema 动态构造 Pydantic args_schema
        params = definition.get("parameters", {})
        fields = {}
        for prop_name, prop_schema in params.get("properties", {}).items():
            prop_type = self._json_type_to_python(prop_schema.get("type", "string"))
            default = prop_schema.get("default", ...)
            description = prop_schema.get("description", "")
            if default is not ...:
                fields[prop_name] = (prop_type, default)
            else:
                fields[prop_name] = (prop_type, ...)
            # Pydantic Field description 通过 Annotated 注入

        ArgsModel = create_model(
            f"{definition['name']}_args",
            **fields,
        )

        return StructuredTool.from_function(
            name=definition["name"],
            description=definition["description"],
            args_schema=ArgsModel,
            coroutine=execute_func,
            metadata=definition.get("metadata", {}),
        )

    @staticmethod
    def _json_type_to_string(json_type: str) -> str:
        mapping = {
            "string": "str", "integer": "int", "number": "float",
            "boolean": "bool", "array": "list", "object": "dict",
        }
        return mapping.get(json_type, "str")
```

### 启动加载流程

```
main.py 启动
  │
  ├─ 1. ToolFileStore.list_definitions("native")
  │     → 读取 tools/definitions/native/*.json
  │     → 获得工具元数据列表
  │
  ├─ 2. 链接执行函数
  │     for def in native_defs:
  │         execute_func = BUILTIN_EXECUTORS[def["name"]]  # 代码中预定义的执行函数映射
  │         tool = store.build_tool_from_definition(def, execute_func)
  │         registry.register(tool, category="native")
  │
  ├─ 3. ToolFileStore.list_definitions("mcp")
  │     → 读取 tools/definitions/mcp/servers.yaml
  │     → 通过 langchain-mcp-adapters 连接 MCP Servers
  │     → 将 MCP 工具注册到 registry (category="mcp")
  │
  ├─ 4. 加载 Skills
  │     → 读取 tools/skills/*.yaml
  │     → 构建 SkillPipeline 并注册
  │
  ├─ 5. 构建 RAG 向量索引
  │     → ToolVectorStore.index_tools(registry.list_all())
  │     → 写入 tools/vectors/ (ChromaDB)
  │
  └─ 6. registry.freeze()
        → 启动后禁止新增工具
```

---

## MCP 协议集成

### 架构

通过 `langchain-mcp-adapters` 连接外部 MCP Server，自动将 MCP 工具转换为 LangChain `BaseTool`。

```
MCP Server (外部进程)
  │  stdio / SSE / HTTP
  ▼
langchain_mcp_adapters.MultiServerMCPClient
  │  list_tools() → BaseTool 列表
  │  call_tool()  → 执行结果
  ▼
tools/adapters/mcp.py
  │  MCPToolManager: 管理 MCP Server 连接生命周期
  │  load_mcp_tools() → 注册到 ToolRegistry
  ▼
ToolRegistry._mcp_tools
```

### MCP Server 配置

```yaml
# tools/definitions/mcp/servers.yaml
servers:
  filesystem:
    command: "npx"
    args: ["-y", "@anthropic-ai/mcp-server-filesystem", "/workspace"]
    description: "File system operations — read, write, list files"

  fetch:
    command: "npx"
    args: ["-y", "@anthropic-ai/mcp-server-fetch"]
    description: "HTTP fetch — retrieve web page content"

  github:
    command: "npx"
    args: ["-y", "@anthropic-ai/mcp-server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
    description: "GitHub operations — issues, PRs, repos"
```

### MCP 管理器实现

```python
# tools/adapters/mcp.py
from typing import List, Dict
import os
import yaml
from pathlib import Path
from langchain_core.tools import BaseTool
from tools.adapters.langchain_adapter import wrap_langchain_tool


class MCPToolManager:
    """MCP Server 连接管理器 —— 基于 langchain-mcp-adapters。

    职责:
      1. 从 servers.yaml 读取 MCP Server 配置
      2. 建立 MultiServerMCPClient 连接
      3. 获取工具列表并适配为 HpAgent ToolResult
      4. 管理连接生命周期（start / stop）
    """

    def __init__(self, config_path: str = "tools/definitions/mcp/servers.yaml"):
        self._config_path = Path(config_path)
        self._client = None
        self._servers_config: Dict = {}

    async def load_config(self) -> Dict:
        """加载 servers.yaml 并解析环境变量。"""
        raw = self._config_path.read_text(encoding="utf-8")
        # 替换 ${ENV_VAR} 占位符
        import re
        def _resolve_env(match):
            return os.environ.get(match.group(1), "")
        raw = re.sub(r'\$\{(\w+)\}', _resolve_env, raw)
        self._servers_config = yaml.safe_load(raw)
        return self._servers_config

    async def connect(self) -> None:
        """连接所有 MCP Server。"""
        from langchain_mcp_adapters.client import MultiServerMCPClient

        servers = self._servers_config.get("servers", {})
        server_configs = {}
        for name, cfg in servers.items():
            server_configs[name] = {
                "command": cfg.get("command"),
                "args": cfg.get("args", []),
                "env": cfg.get("env", {}),
            }

        self._client = MultiServerMCPClient(server_configs)

    async def list_tools(self) -> List[BaseTool]:
        """获取所有 MCP Server 提供的工具列表。

        Returns:
            已适配为 HpAgent ToolResult 的 LangChain BaseTool 列表。
        """
        if self._client is None:
            await self.connect()

        tools = []
        for server_name in self._servers_config.get("servers", {}):
            try:
                server_tools = await self._client.get_tools(server_name)
                for tool in server_tools:
                    tool = wrap_langchain_tool(tool)
                    # 标记工具来源
                    tool.metadata = tool.metadata or {}
                    tool.metadata["mcp_server"] = server_name
                    tool.metadata["category"] = "mcp"
                    tools.append(tool)
            except Exception as e:
                import logging
                logging.getLogger("HpAgent.MCP").warning(
                    "Failed to list tools from MCP server '%s': %s", server_name, e
                )
        return tools

    async def disconnect(self) -> None:
        """断开所有 MCP Server 连接。"""
        if self._client:
            await self._client.close()
            self._client = None
```

---

## Skills 复合工具编排

### 概念

Skill 是将多个工具组合为高级能力的"元工具"——对 LLM 暴露为单个工具，内部由子工具编排流水线执行。

```
LLM 看到的:                    实际执行的:
┌──────────────┐          ┌─────────────────────┐
│ daily_report │          │ 1. web_search("新闻")│
│  (一个工具)   │   →     │ 2. file_read("模板") │
└──────────────┘          │ 3. calculator()      │
                          │ 4. LLM 汇总          │
                          └─────────────────────┘
```

### Skill 定义格式

```yaml
# tools/skills/daily_report.yaml
name: daily_report
description: >
  Generate a daily briefing report — searches news, reads templates,
  and compiles a formatted summary.
category: skill

parameters:
  type: object
  properties:
    topic:
      type: string
      description: Report topic or focus area
    date:
      type: string
      description: Target date for the report (YYYY-MM-DD)
  required: ["topic"]

pipeline:
  steps:
    - id: search_news
      tool: web_search
      arguments:
        query: "$topic latest news $date"

    - id: read_template
      tool: file_read
      arguments:
        file_path: "/workspace/templates/report_template.md"

    - id: compile
      tool: _llm_synthesize       # 特殊步骤：调用 LLM 汇总前两步结果
      template: |
        Based on the following news and template, generate a daily report.
        Topic: $topic
        Date: $date
        News: $search_news
        Template: $read_template

on_error: stop                    # stop | continue | fallback_tool
timeout_seconds: 60
```

### SkillPipeline 实现

```python
# tools/skills/engine.py
from typing import List, Dict, Any
from langchain_core.tools import BaseTool, StructuredTool
from tools.types import ToolResult


class SkillPipeline:
    """Skill 编排引擎 —— 按流水线步骤顺序执行工具调用。

    支持:
      - 步骤间结果引用: $step_id 引用前一步骤的输出
      - 参数模板替换: $param_name 引用 Skill 输入参数
      - 错误策略: stop / continue / fallback_tool
      - 超时控制: per-step 和全局超时
    """

    def __init__(
        self,
        name: str,
        description: str,
        steps: List[dict],
        on_error: str = "stop",
        timeout_seconds: float = 60.0,
    ):
        self._name = name
        self._description = description
        self._steps = steps
        self._on_error = on_error
        self._timeout = timeout_seconds

    async def execute(self, registry, **kwargs) -> ToolResult:
        """执行 Skill 流水线。

        Args:
            registry: ToolRegistry 实例，用于查找和调用子工具。
            **kwargs: Skill 级别的输入参数。

        Returns:
            ToolResult，包含所有步骤的输出。
        """
        step_outputs: Dict[str, Any] = {}
        step_outputs.update(kwargs)  # Skill 输入参数也可被引用

        for step in self._steps:
            tool_name = step["tool"]
            arguments = self._resolve_arguments(step.get("arguments", {}), step_outputs)

            tool = registry.get(tool_name)
            if tool is None:
                result = ToolResult(success=False, error=f"Skill step tool '{tool_name}' not found")
            else:
                result = await registry.execute(tool_name, arguments)

            step_outputs[step["id"]] = result.output if result.success else result.error

            if not result.success and self._on_error == "stop":
                return ToolResult(
                    success=False,
                    error=f"Step '{step['id']}' failed: {result.error}",
                    metadata={"step_id": step["id"], "step_outputs": step_outputs},
                )

        return ToolResult(
            success=True,
            output=step_outputs,
            metadata={"steps_completed": len(self._steps)},
        )

    @staticmethod
    def _resolve_arguments(arguments: dict, context: dict) -> dict:
        """解析参数模板中的 $variable 引用。"""
        resolved = {}
        for key, value in arguments.items():
            if isinstance(value, str) and "$" in value:
                import re
                def _replace(m):
                    var_name = m.group(1)
                    return str(context.get(var_name, m.group(0)))
                resolved[key] = re.sub(r'\$(\w+)', _replace, value)
            else:
                resolved[key] = value
        return resolved


def build_skill_tool(pipeline: SkillPipeline, registry) -> BaseTool:
    """将 SkillPipeline 包装为 LangChain BaseTool。

    LLM 将其视为普通工具调用，内部走流水线编排。
    """
    async def _execute_skill(**kwargs) -> ToolResult:
        return await pipeline.execute(registry, **kwargs)

    return StructuredTool.from_function(
        name=pipeline._name,
        description=pipeline._description,
        coroutine=_execute_skill,
    )
```

---

## 执行器与沙箱

执行器与 LangChain 解耦 —— LangChain 管理工具元数据和调用链，执行器管理实际执行环境。

```python
# tools/executor.py
from typing import Protocol
from tools.types import ToolResult


class BaseToolExecutor(Protocol):
    """工具执行器 Protocol —— 与 LangChain 解耦的隔离执行后端。"""

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        ...

    async def setup(self) -> None:
        ...

    async def teardown(self) -> None:
        ...


class InProcessExecutor:
    """进程内执行器 —— 开发环境使用，直接调用 ToolRegistry.execute()。"""

    def __init__(self, registry):
        self._registry = registry

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        return await self._registry.execute(tool_name, arguments)

    async def setup(self) -> None: pass
    async def teardown(self) -> None: pass
```

NsjailExecutor 保留现有实现（`src/sandbox/nsjail.py`），包装为符合 `BaseToolExecutor` Protocol 的接口。

---

## Agent Loop 集成

### HarnessRunner 改造

现有的 `HarnessRunner.process_turn()` 中工具相关调用改为使用新 ToolRegistry：

```python
# harness/runner.py (改动部分)

class HarnessRunner:
    def __init__(self, ..., tool_registry: ToolRegistry):
        ...
        self._tool_registry = tool_registry

    async def _get_tools_for_turn(self, user_content: str) -> List[dict]:
        """获取本轮对话的可用工具列表。

        如果启用 RAG，按用户意图语义检索 Top-K 工具；
        否则返回所有已注册工具。
        """
        if self._tool_registry._retriever is not None:
            return await self._tool_registry._retriever.retrieve_for_llm(
                query=user_content,
                registry=self._tool_registry,
                top_k=8,
            )
        return self._tool_registry.list_for_llm()

    async def process_turn(self, user_message: dict) -> dict:
        ...
        while turns_taken < self._max_tool_turns:
            turns_taken += 1

            # 召回长期记忆
            memories_items, memories_text = await self._session.recall_memories(...)

            # 构建上下文
            context = self._build_context(events, channel_type, memories_text)

            # ★ RAG 动态工具注入
            tools = await self._get_tools_for_turn(user_content)

            # 调用模型（tools 已经是 Top-K 相关工具）
            response = await self._model.generate(
                model_selector="chat",
                messages=context,
                tools=tools if tools else None,
                stream=False,
            )

            # ★ 执行工具调用（统一走 registry）
            if response.tool_calls:
                for tc in response.tool_calls:
                    result = await self._tool_registry.execute(tc.name, tc.arguments)
                    ...
                continue
            else:
                break
        ...
```

### 工具调用全链路

```
1. 用户消息 → process_turn()

2. RAG 检索
   registry._retriever.retrieve_for_llm(query=user_content, top_k=8)
   → ChromaDB 语义搜索
   → 返回 Top-8 BaseTool
   → 转为 OpenAI function calling 格式

3. LLM 推理
   model.generate(messages=context, tools=top_8_tools)
   → LLM 选择工具（因为选项少，选择更准确）
   → 返回 tool_call {name: "calculator", arguments: {expression: "3.14*2.5"}}

4. 工具执行
   registry.execute("calculator", {expression: "3.14*2.5"})
   → registry.get("calculator") → BaseTool 实例
   → tool.ainvoke(arguments) → LangChain 内部执行
   → 适配器包装为 ToolResult

5. 结果注入 → 下一轮 LLM 推理（或结束 loop）
```

---

## 配置与启动流程

### 新增配置项

```yaml
# config/models.yaml (新增部分)

# ── 工具 RAG 检索配置 ──
tool_rag:
  enabled: true                 # 是否启用 RAG 动态注入
  top_k: 8                      # 每轮注入的工具数量
  vector_store: "chromadb"      # 向量存储后端
  persist_path: "tools/vectors" # ChromaDB 持久化路径
  embedding_model:              # 工具向量化的模型（复用 embedding 降级链或独立指定）
    provider: minimax
    model: "embo-01"

# ── MCP Server 配置 ──
mcp:
  config_path: "tools/definitions/mcp/servers.yaml"
  auto_connect: true            # 启动时自动连接
  connection_timeout: 30.0

# ── Skills 配置 ──
skills:
  config_path: "tools/skills/"
  enabled: true
```

### 主启动流程

```python
# main.py (新增部分)

async def setup_tools(config: dict) -> ToolRegistry:
    """启动时完整初始化工具体系。

    顺序:
      1. 文件存储 → 加载 JSON 工具定义
      2. 代码链接 → build_tool_from_definition 连接执行函数
      3. MCP 连接 → 加载 MCP Server 工具
      4. Skills 加载 → 解析 YAML 构建 SkillPipeline
      5. RAG 索引 → 向量化所有工具并写入 ChromaDB
      6. Freeze   → 注册封禁
    """
    from tools.registry import ToolRegistry
    from tools.store import ToolFileStore
    from tools.retriever import ToolVectorStore, ToolRetriever
    from tools.adapters.mcp import MCPToolManager
    from tools.skills.engine import build_skill_tool

    store = ToolFileStore(base_path="tools/definitions")

    # 1. RAG 初始化
    vector_store = None
    retriever = None
    rag_cfg = config.get("tool_rag", {})
    if rag_cfg.get("enabled", True):
        from resources.embedding import EmbeddingClient
        emb_client = EmbeddingClient(config["embedding"])  # 复用 models.yaml 中的 embedding 配置链
        vector_store = ToolVectorStore(persist_path=rag_cfg.get("persist_path", "tools/vectors"))
        retriever = ToolRetriever(vector_store, emb_client)

    registry = ToolRegistry(retriever=retriever)

    # 2. 加载本地内置工具
    from tools.builtin import BUILTIN_EXECUTORS
    for tool_def in store.list_definitions("native"):
        name = tool_def["name"]
        if name in BUILTIN_EXECUTORS:
            tool = store.build_tool_from_definition(tool_def, BUILTIN_EXECUTORS[name])
            registry.register(tool, category="native")

    # 3. 加载自定义工具
    for tool_def in store.list_definitions("custom"):
        if tool_def.get("type") == "http":
            tool = store.build_http_tool(tool_def)
            registry.register(tool, category="native")

    # 4. 连接 MCP Server
    mcp_cfg = config.get("mcp", {})
    if mcp_cfg.get("auto_connect", True):
        mcp_manager = MCPToolManager(
            config_path=mcp_cfg.get("config_path", "tools/definitions/mcp/servers.yaml")
        )
        mcp_tools = await mcp_manager.list_tools()
        for tool in mcp_tools:
            registry.register(tool, category="mcp")

    # 5. 加载 Skills
    import yaml
    skills_path = Path(config.get("skills", {}).get("config_path", "tools/skills/"))
    for skill_file in skills_path.glob("*.yaml"):
        skill_def = yaml.safe_load(skill_file.read_text())
        pipeline = SkillPipeline(
            name=skill_def["name"],
            description=skill_def["description"],
            steps=skill_def["pipeline"]["steps"],
            on_error=skill_def.get("on_error", "stop"),
            timeout_seconds=skill_def.get("timeout_seconds", 60.0),
        )
        skill_tool = build_skill_tool(pipeline, registry)
        registry.register(skill_tool, category="skill")

    # 6. 构建 RAG 向量索引
    if vector_store and rag_cfg.get("enabled", True):
        from resources.embedding import EmbeddingClient
        emb_client = EmbeddingClient(config["embedding"])
        vector_store.index_tools(registry.list_all(), emb_client)

    # 7. 封禁注册
    registry.freeze()

    return registry
```

---

## 与现有代码的关系

### 保留不变（仅改引用路径）

| 现有文件 | 改动 |
|---------|------|
| `common/types.py` — `ToolCall`, `ToolResult` 定义 | `ToolCall` 保留不变。旧 `ToolResult` 加 DeprecationWarning，新代码统一用 `tools/types.py` |
| `sandbox/nsjail.py` — NsjailExecutor | 包装为符合 `BaseToolExecutor` Protocol |
| `sandbox/sandbox.py` — Sandbox | 内部 `ToolRegistry` 替换为新的 LangChain 版本 |
| `sandbox/sandbox_manager.py` | `create_sandbox()` 参数改为 LangChain `BaseTool` 列表 |
| `harness/runner.py` — HarnessRunner | `_get_tools()` 改为 RAG 检索；`_execute_tool()` 改为走 registry |
| `main.py` | 新增 `setup_tools()` 调用 |

### 废弃删除

| 文件 | 原因 |
|------|------|
| `sandbox/tools/base.py` | 替换为 `langchain_core.tools.BaseTool` |
| `sandbox/tools/registry.py` | 替换为新的 `tools/registry.py`（LangChain 集成版） |
| `sandbox/tools/factory.py` | `DynamicTool` 废弃，替代为 `StructuredTool.from_function()` + `@tool` |
| `sandbox/tools/__init__.py` | 目录整体迁移到 `tools/` |

### 新增文件

| 文件 | 用途 |
|------|------|
| `tools/store.py` | 本地文件存储管理器 |
| `tools/retriever.py` | RAG 向量存储 + 检索器 |
| `tools/adapters/langchain_adapter.py` | LangChain → HpAgent ToolResult 适配 |
| `tools/adapters/mcp.py` | MCP Server 连接管理器 |
| `tools/skills/engine.py` | SkillPipeline 编排引擎 |
| `tools/definitions/` | JSON/YAML 工具定义文件 |
| `tools/vectors/` | ChromaDB 向量持久化 |

### 新增依赖

```
# requirements.txt 新增
langchain-core>=0.3.0
langchain-community>=0.3.0
langchain-mcp-adapters>=0.1.0
chromadb>=0.5.0
pydantic>=2.0
pyyaml>=6.0
```

---

## 现有关键问题与修复

> 基于代码审计发现的、需要在迁移中一并修复的问题。

### 问题一：两套不兼容的 ToolResult 类型

**现状**：项目中存在两个字段不同的 `ToolResult` 类：

| 位置 | 字段 | 用途 |
|------|------|------|
| `common/types.py` | `tool_call_id`, `status` ("success"/"error"), `content`, `error` | Agent loop 事件系统 |
| `sandbox/tools/base.py` | `success` (bool), `output`, `error`, `metadata` | 工具执行层 |

两者字段不兼容，`HarnessRunner._execute_tool()` 内部通过 `to_dict()` / dict 解包做隐式转换（`harness/runner.py:349-352`），容易丢失数据。

**修复**：迁移后统一为 `tools/types.py` 中的单一定义：

```python
@dataclass
class ToolResult:
    success: bool = True
    output: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
```

`common/types.py` 中的旧 `ToolResult` 保留但加 `DeprecationWarning`，由 `HarnessRunner` 在 event 构造处做一次性转换。

### 问题二：HarnessRunner._execute_tool() 返回格式不一致

**现状** (`harness/runner.py:336-357`)：遍历沙箱执行工具后，通过 `to_dict()` 或手动构造 `{"output": ..., "error": ...}` 返回 dict。ToolRegistry 的 `execute()` 期望返回 ToolResult，但 HarnessRunner 绕过了它。

**修复**：统一为 `self._tool_registry.execute(tool_name, arguments)` → 直接拿到 ToolResult，不再遍历沙箱。

### 问题三：_get_tools() 每轮遍历所有沙箱

**现状** (`harness/runner.py:321-334`)：每次 LLM 推理前遍历 `SandboxManager.list_sandboxes()`，对每个活跃沙箱调用 `sandbox.list_tools()`。工具列表不缓存，随着沙箱数量增长会产生不必要的开销。

**修复**：ToolRegistry 作为单一数据源，`list_for_llm()` 是 O(1) 的 dict values 遍历，无需跨沙箱收集。RAG 模式下更是只返回 Top-K 结果。

### 问题四：多 Agent 模式下工具执行未连接

**现状** (`agent/runner.py`)：`LLMAgent._run_loop()` 中 `_tool_executor` 回调被设为 `None`，多 Agent 路径无法执行工具调用。

**修复**：将 `ToolRegistry` 注入 `MultiAgentExecutor`，替换 `_tool_executor` 回调。

### 问题五：web_search 返回模拟数据

**现状** (`sandbox/tools/factory.py:171-178`)：`create_search_tool()` 返回硬编码的 `example.com` 假结果。

**修复**：接入真实搜索 API（Tavily / Bing / SerpAPI），通过 LangChain 社区工具包直接使用：

```python
from langchain_community.tools.tavily_search import TavilySearchResults
web_search = TavilySearchResults(max_results=5)
```

---

## 迁移检查清单

### Phase 1: 核心抽象替换

- [ ] 安装 `langchain-core`, `langchain-mcp-adapters`, `chromadb`
- [ ] 创建 `tools/types.py` — 保留 ToolResult
- [ ] 创建 `tools/adapters/langchain_adapter.py` — `wrap_langchain_tool()`
- [ ] 创建 `tools/registry.py` — 新的三槽位 ToolRegistry
- [ ] 迁移 `sandbox/tools/builtin/` → 用 `@tool` 装饰器重写
- [ ] 创建 `tools/definitions/native/*.json` — 工具元数据文件

### Phase 2: 存储与 RAG

- [ ] 创建 `tools/store.py` — ToolFileStore
- [ ] 创建 `tools/retriever.py` — ToolVectorStore + ToolRetriever
- [ ] 创建 `tools/vectors/` 目录，添加 `.gitkeep`
- [ ] 在 `config/models.yaml` 添加 `tool_rag` 配置节
- [ ] 创建 `resources/embedding.py` — 复用现有 embedding 配置链

### Phase 3: MCP 与 Skills

- [ ] 创建 `tools/adapters/mcp.py` — MCPToolManager
- [ ] 创建 `tools/definitions/mcp/servers.yaml` — MCP Server 配置模板
- [ ] 创建 `tools/skills/engine.py` — SkillPipeline
- [ ] 创建 `tools/skills/*.yaml` — 示例 Skill 定义

### Phase 4: 执行器适配

- [ ] 创建 `tools/executor.py` — BaseToolExecutor Protocol + InProcessExecutor
- [ ] 将 `NsjailExecutor` 包装为符合 Protocol
- [ ] 更新 `Sandbox` — 内部使用新 ToolRegistry
- [ ] 更新 `SandboxManager` — create_sandbox() 接受 LangChain BaseTool

### Phase 5: Agent Loop 集成

- [ ] 更新 `HarnessRunner._get_tools_for_turn()` — RAG 动态注入
- [ ] 更新 `HarnessRunner._execute_tool()` — 统一走 registry.execute()
- [ ] 更新 `HarnessRunner.process_turn()` — 集成 RAG + 新 registry
- [ ] 更新 `main.py` — 添加 `setup_tools()` 启动流程
- [ ] 更新 `sandbox/channels/` — 适配新工具接口

### Phase 6: 清理与测试

- [ ] 删除 `sandbox/tools/base.py`、`registry.py`、`factory.py`
- [ ] 更新 `sandbox/tools/__init__.py` — 重新导出
- [ ] 为每个内置工具编写单元测试
- [ ] ToolRegistry 集成测试 — register / get / execute / RAG 检索
- [ ] MCP 连接集成测试 — 连接真实 MCP Server
- [ ] SkillPipeline 编排测试 — 正常 / 错误 / 超时路径
- [ ] 端到端测试 — HarnessRunner 完整 agentic loop

---

## 关键设计决策

| 决策 | 说明 |
|------|------|
| **LangChain BaseTool 作为工具基类** | 不再维护自建 ABC，直接使用 LangChain 生态的 Pydantic 校验 + 自动 Schema 生成 |
| **ToolResult 保留** | LangChain 没有等价的结构化 result wrapper，保留用于携带 metadata / error 上下文 |
| **RAG 动态注入代替全量注入** | 工具数量增长后全量注入浪费 token 且降低 LLM 选择准确率 |
| **文件即真相** | JSON/YAML 文件存储工具定义，便于版本控制、审计、非代码人员维护 |
| **MCP 通过 langchain-mcp-adapters** | 不做自定义 MCP 协议栈，复用社区维护的适配器 |
| **Skills 基于流水线编排** | YAML 定义的步骤式编排而非图式编排，降低复杂度 |
| **执行器与 LangChain 解耦** | LangChain 管元数据 + 调用链，执行器管隔离执行（nsjail/进程内/Docker） |
| **启动时 Freeze** | 继承现有设计，注册阶段结束后封禁，防止运行时注入 |
| **三槽位注册** | 本地 / MCP / Skill 三槽位独立管理，支持按类别过滤和计量 |
