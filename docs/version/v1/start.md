# OpenClaw 风格自动回复框架 —— Python 重构需求规格说明书

## 1. 项目概述

### 1.1 项目名称
`HpAgent`

### 1.2 项目目标
基于 OpenClaw 自动回复框架的核心设计模式，用 Python 3.11+ 实现一个**可独立运行**的单渠道对话回复引擎。第一阶段聚焦于**从用户输入到 AI 回复**的完整同步流水线，不引入异步、流式、多会话等高级特性，为后续迭代打下清晰、可测试的基座。

### 1.3 第一版范围（MVP）
| 包含 | 不包含 |
|------|--------|
| 接收一条纯文本用户消息 | 多渠道支持（仅内置 `ConsoleChannel` 模拟） |
| 构建包含会话历史的上下文 | 真正的持久化存储（仅内存模拟） |
| 调用一个 OpenAI 兼容模型生成回复 | 流式输出、工具调用、命令解析 |
| 模型调用失败时的简单重试机制 | 复杂的模型回退策略（留接口） |
| 返回最终回复文本 | 块级回复、typing 指示器、防抖去重 |

### 1.4 设计原则
1. **分层解耦**：清晰分离入口、上下文构建、执行、回复处理层。
2. **依赖注入**：所有外部依赖（模型调用器、会话存储、配置）通过参数传入。
3. **可测试性**：核心逻辑纯函数化，边界通过接口抽象。
4. **预留扩展点**：使用 `Protocol` 定义未来可替换组件。

---

## 2. 技术选型与约束

| 项 | 选择 | 说明 |
|----|------|------|
| Python 版本 | 3.11+ | 使用 `asyncio` 但 MVP 用同步包装 |
| 类型检查 | `mypy` / `pyright` | 严格模式，利用 `dataclass` 和 `Protocol` |
| 依赖管理 | `poetry` 或 `pip` + `requirements.txt` | 推荐 poetry 便于后续打包 |
| HTTP 客户端 | `httpx` | 同步 + 异步双支持，模型调用用同步模式 |
| 测试框架 | `pytest` | 单元测试 + 简单集成测试 |
| 日志 | `logging` 标准库 | 结构化日志预留 |
| 配置管理 | `pydantic` 或 `dataclass` | MVP 用 `dataclass` 字典映射 |

---

## 3. 架构设计概览（分层）

遵循 OpenClaw 文档中的分层思想，简化如下：

```
┌─────────────────────────────────────────┐
│          Entry Layer                    │
│  main.py: 接收消息，初始化配置，调用编排  │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│          Context Layer                  │
│  context_builder.py: 构建 TemplateContext│
│  session_store.py (内存版): 管理对话历史  │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│          Execution Layer                │
│  agent_runner.py: run_reply_agent()     │
│  model_executor.py: 调用模型并处理重试   │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│          Response Layer                 │
│  payload_builder.py: 构建 ReplyPayload   │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│          Delivery Layer                 │
│  console_channel.py: 打印回复（模拟投递） │
└─────────────────────────────────────────┘
```

---

## 4. 核心数据结构定义

请编码 agent 严格按照以下 Python 定义生成初始文件。

### 4.1 `core/types.py`

```python
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class TemplateContext:
    """贯穿整个回复流程的上下文对象（简化版）"""
    body: str                                    # 用户原始消息
    session_key: str                             # 会话唯一标识（如 "console_user_123"）
    provider: str = "console"                     # 来源渠道标识
    from_: Optional[str] = None                   # 发送者 ID
    to: Optional[str] = None                      # 接收者/机器人 ID
    reply_to_id: Optional[str] = None             # 回复目标消息 ID（MVP 忽略）
    media_urls: List[str] = field(default_factory=list)  # 媒体链接（MVP 忽略）
    chat_type: str = "direct"                     # "direct" / "group"
    # 以下字段用于 AI 提示词构建
    conversation_history: List[Dict[str, str]] = field(default_factory=list)  
    # 格式：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]

@dataclass
class ReplyPayload:
    """最终回复的内容载体"""
    text: str
    is_error: bool = False
    # 后续可扩展：media_urls, reply_to_id 等
```

### 4.2 配置结构 `core/config.py`

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ModelConfig:
    provider: str = "openai"          # 未来可扩展 anthropic 等
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: Optional[str] = None    # 兼容本地模型或代理
    max_retries: int = 2
    timeout_seconds: int = 30

@dataclass
class AppConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    max_history_turns: int = 10       # 会话历史保留轮数
    system_prompt: str = "You are a helpful assistant."
```

---

## 5. 模块详细设计（指导编码 agent 生成）

### 5.1 会话存储（内存模拟）`context/session_store.py`

**职责**：存储每个会话的对话历史（`List[Dict]`），提供增删改查接口。

**接口定义**：

```python
from typing import Dict, List, Optional
from threading import RLock

class SessionStore:
    def __init__(self):
        self._storage: Dict[str, List[Dict[str, str]]] = {}
        self._lock = RLock()
    
    def get_history(self, session_key: str) -> List[Dict[str, str]]:
        """返回会话历史列表副本，若无则返回空列表"""
        ...
    
    def append_turn(self, session_key: str, user_msg: str, assistant_msg: str) -> None:
        """追加一轮对话（user + assistant）"""
        ...
    
    def clear(self, session_key: str) -> None:
        """清除指定会话历史"""
        ...
```

**验收标准**：
- 线程安全（使用 `RLock`）
- 单例模式可选（MVP 中作为依赖传入即可）

### 5.2 上下文构建器 `context/context_builder.py`

**职责**：接收原始消息和会话存储，构建完整的 `TemplateContext`。

**函数签名**：

```python
def build_context(
    user_message: str,
    session_key: str,
    session_store: SessionStore,
    system_prompt: str,
    max_history_turns: int,
) -> TemplateContext:
    """
    步骤：
    1. 从 session_store 获取历史
    2. 截断历史至 max_history_turns * 2 条消息（user+assistant 交替）
    3. 在历史最前方插入 system prompt（格式：{"role": "system", "content": ...}）
    4. 返回 TemplateContext，其中 conversation_history 为已处理的列表
    """
```

**学习点**：此处体现了“上下文聚合”职责，是未来支持 RAG、记忆压缩的插入点。

### 5.3 模型执行器 `execution/model_executor.py`

**职责**：封装对 OpenAI 兼容 API 的调用，包含简单重试逻辑。

**接口定义**：

```python
from core.types import TemplateContext
from core.config import ModelConfig

class ModelExecutor:
    def __init__(self, config: ModelConfig):
        self.config = config
        self._client = None  # 延迟初始化 httpx.Client
    
    def generate(self, context: TemplateContext) -> str:
        """
        发送 conversation_history 到模型 API，返回回复文本。
        若失败则按 config.max_retries 重试，最终失败抛出 ModelError。
        """
        ...
```

**异常定义**：

```python
class ModelError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        ...
```

**HTTP 请求格式（OpenAI 兼容）**：

```json
{
  "model": "gpt-4o-mini",
  "messages": [{"role": "system", ...}, {"role": "user", ...}, ...],
  "temperature": 0.7
}
```

**学习点**：此处是策略模式（重试策略）和适配器模式的雏形，后续可扩展为多模型回退。

### 5.4 回复构建器 `response/payload_builder.py`

**职责**：将模型返回的文本包装成 `ReplyPayload`。

```python
def build_reply_payload(model_response: str, is_error: bool = False) -> ReplyPayload:
    return ReplyPayload(text=model_response, is_error=is_error)
```

**学习点**：虽然简单，但为后续添加媒体处理、前缀过滤留出位置。

### 5.5 核心编排器 `execution/agent_runner.py`

**职责**：`run_reply_agent` 函数是框架心脏，协调以上所有组件。

**函数签名**：

```python
from core.config import AppConfig
from core.types import ReplyPayload
from context.session_store import SessionStore

def run_reply_agent(
    user_message: str,
    session_key: str,
    config: AppConfig,
    session_store: SessionStore,
    model_executor: ModelExecutor,
) -> ReplyPayload:
    """
    主流程：
    1. 调用 build_context 构建 TemplateContext
    2. 调用 model_executor.generate(context) 获取回复文本
    3. 若成功，将本轮对话存入 session_store.append_turn(...)
    4. 调用 build_reply_payload 返回结果
    5. 若模型调用失败，返回一个 is_error=True 的 ReplyPayload，且不存入历史
    """
```

**异常处理**：
- 捕获 `ModelError`，记录日志，返回错误提示 ReplyPayload。
- 其他异常同样处理，避免框架崩溃。

### 5.6 模拟渠道 `delivery/console_channel.py`

**职责**：模拟消息接收和回复发送，提供交互式测试入口。

```python
class ConsoleChannel:
    def __init__(self, config: AppConfig, session_store: SessionStore, model_executor: ModelExecutor):
        self.config = config
        self.session_store = session_store
        self.model_executor = model_executor
    
    def start(self):
        """循环读取用户输入，调用 run_reply_agent，打印回复。输入 'exit' 退出。"""
        ...
```

---

## 6. 接口约定与依赖注入

为了便于测试和替换，所有关键组件均通过构造函数或函数参数注入，**不使用全局单例（除非显式需要）**。

示例：

```python
# main.py 组装
config = AppConfig(
    model=ModelConfig(api_key=os.getenv("OPENAI_API_KEY"))
)
session_store = SessionStore()
model_executor = ModelExecutor(config.model)
channel = ConsoleChannel(config, session_store, model_executor)
channel.start()
```

---

## 7. 测试策略（编码 agent 可生成对应测试文件）

### 7.1 单元测试 `tests/`
| 模块 | 测试重点 |
|------|----------|
| `SessionStore` | 并发安全、历史截断逻辑 |
| `context_builder` | 历史截断、system prompt 插入正确性 |
| `model_executor` | 使用 mock 模拟 HTTP 响应，验证重试次数和异常处理 |
| `agent_runner` | 集成 mock 组件验证流程正确性，错误时不写入历史 |

### 7.2 集成测试
- 使用一个真实（或本地 mock）的模型 API 端到端验证一次完整对话。

---

## 8. 扩展点与未来迭代方向

在第一版代码中需预留以下扩展点（通过接口或注释标记）：

| 扩展点 | 当前占位 | 未来方向 |
|--------|----------|----------|
| 多渠道支持 | `TemplateContext.provider` | 抽象 `ChannelAdapter` 接口 |
| 流式输出 | 无 | 修改 `ModelExecutor.generate` 返回生成器，调整编排器支持回调 |
| 命令系统 | 无 | 在 `context_builder` 中识别 `/` 开头消息，分流到命令处理器 |
| 模型回退 | `ModelExecutor` 固定单一模型 | 改为接受模型列表，实现 `run_with_fallback` |
| 持久化存储 | 内存 `SessionStore` | 实现 `RedisSessionStore`、`FileSessionStore` |
| 工具调用 | 无 | 扩展 `ModelExecutor` 支持 function calling 循环 |
| 记忆压缩 | 无 | 在 `context_builder` 中插入压缩钩子 |

在代码中以注释形式标注 `# TODO: Extension point - xxx`，方便学习时追踪。

---

## 9. 学习路线与软件工程要点

建议按以下顺序阅读和实践：

1. **运行第一个版本**：仅实现 `ConsoleChannel` 和 `ModelExecutor`，能调用 API 回复。
2. **加入会话历史**：实现 `SessionStore`，观察上下文对回复的影响。
3. **重构分层**：按照本文档将代码拆分到各模块，体会**分层解耦**的好处。
4. **编写单元测试**：为 `context_builder` 和 `SessionStore` 编写测试，理解**依赖注入**如何简化测试。
5. **添加错误处理与重试**：完善 `ModelExecutor`，学习**重试策略**与**异常分类**。
6. **分析扩展点**：对比 OpenClaw 原始文档，思考下一步应实现哪个特性。

### 软件工程概念地图

| 概念 | 在本项目中的体现 |
|------|------------------|
| **分层架构** | Entry → Context → Execution → Response → Delivery |
| **依赖注入** | `run_reply_agent` 接收 `session_store` 和 `model_executor` |
| **策略模式** | `ModelExecutor` 内部的重试策略（可替换为指数退避） |
| **管道模式** | 从用户输入到回复的线性处理链（未来可扩展为中间件） |
| **单一职责原则** | 每个模块只做一件事（存储、构建、调用、编排） |

---

## 11. 项目结构建议

```
py-reply-core/
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   └── config.py
│   ├── context/
│   │   ├── __init__.py
│   │   ├── session_store.py
│   │   └── context_builder.py
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── model_executor.py
│   │   └── agent_runner.py
│   ├── response/
│   │   ├── __init__.py
│   │   └── payload_builder.py
│   ├── delivery/
│   │   ├── __init__.py
│   │   └── console_channel.py
│   └── main.py
├── tests/
│   ├── test_session_store.py
│   ├── test_context_builder.py
│   ├── test_model_executor.py
│   └── test_agent_runner.py
├── pyproject.toml (或 requirements.txt)
├── .env.example
└── README.md
```