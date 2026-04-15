## Anthropic 风格 Managed Agent 架构 —— Python 实现需求规格说明书（第二版）

### 1. 版本迭代目标

本需求文档指导编码 agent 在 **第一版**（单次请求-响应执行器）的基础上，重构并扩展为 **第二版 Agent Harness**。

**第二版核心变更：**
- 从单次模型调用升级为 **Agent Loop（控制循环）**，支持多轮工具调用。
- 引入 **Harness 层** 作为调度中枢，负责持续调用模型并路由工具。
- 建立 **统一事件模型**，使流式输出、工具调用过程可观测、可中断。
- 保持代码 **模块化且单文件长度 < 200 行**。

### 2. 架构总览

遵循 Anthropic 对 Agent 系统的四层划分：

| 层级 | 组件 | 职责 |
|------|------|------|
| **Model** | `ModelClient` | 封装 Anthropic/OpenAI 兼容 API，返回结构化响应与 `stop_reason` |
| **Harness** | `AgentLoop` | 维持 `while` 循环，根据 `stop_reason` 决定继续调用模型或执行工具 |
| **Tools** | `ToolRegistry` + `ToolRouter` | 管理工具定义，执行工具调用并将结果格式化 |
| **Environment** | `SessionStore`（已有） | 提供会话持久化、文件系统等外部资源 |

**数据流图：**

```
┌─────────────────────────────────────────────────────────────┐
│                     Harness Layer                            │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                   AgentLoop                          │    │
│  │  while turn < max_turns:                            │    │
│  │    response = await model.chat(messages, tools)     │    │
│  │    if response.stop_reason == END_TURN: break       │    │
│  │    if response.stop_reason == TOOL_USE:             │    │
│  │      results = await tool_router.route(tool_calls)  │    │
│  │      messages.append(tool_results)                  │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 3. 目录结构（基于第一版扩展）

```
src/execution/
├── __init__.py
├── agent_runner.py            # 编排入口（门面）
├── harness/
│   ├── __init__.py
│   ├── events.py              # 事件定义与 StopReason 枚举
│   ├── loop.py                # AgentLoop 控制循环
│   └── stream_processor.py    # 流式增量处理器（可选）
├── tools/
│   ├── __init__.py
│   ├── registry.py            # 工具注册表
│   ├── router.py              # 工具路由器
│   └── builtin/
│       ├── __init__.py
│       └── calculator.py      # 示例内置工具
├── model/
│   ├── __init__.py
│   ├── client.py              # 模型客户端（含流式解析）
│   └── fallback.py            # 模型回退（可选复用第一版）
└── session/                   # 已有，保持第一版不变
    └── manager.py
```

### 4. 核心模块详细规格

#### 4.1 事件定义 `harness/events.py`

**要求**：定义 Agent 执行过程中所有可观测事件的类型与数据结构。

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    ERROR = "error"

class EventType(str, Enum):
    LOOP_STARTED = "loop_started"
    LOOP_COMPLETED = "loop_completed"
    MODEL_CALLED = "model_called"
    TEXT_DELTA = "text_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TURN_COMPLETED = "turn_completed"
    ERROR = "error"

@dataclass
class ExecutionEvent:
    type: EventType
    turn_index: int
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)
```

#### 4.2 工具系统 `tools/registry.py` & `tools/router.py`

**`registry.py`** - 工具注册表

```python
from typing import Protocol, runtime_checkable, Any
from dataclasses import dataclass, field

@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    async def execute(self, **kwargs) -> Any: ...

@dataclass
class ToolRegistry:
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in self._tools.values()
        ]
```

**`router.py`** - 工具路由器

```python
from typing import Callable, Awaitable, Optional
from .registry import ToolRegistry
from ..harness.events import ExecutionEvent, EventType

class ToolRouter:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    async def route(
        self,
        tool_calls: list[dict],
        turn_index: int,
        on_event: Optional[Callable[[ExecutionEvent], Awaitable[None]]] = None,
    ) -> list[dict]:
        """执行工具调用列表，返回 Anthropic 格式的 tool_result 消息块"""
        results = []
        for tc in tool_calls:
            tool = self.registry.get(tc["name"])
            if not tool:
                results.append(self._error_result(tc["id"], f"Tool not found: {tc['name']}"))
                continue

            if on_event:
                await on_event(ExecutionEvent(
                    type=EventType.TOOL_CALL_STARTED,
                    turn_index=turn_index,
                    timestamp=__import__("time").time(),
                    data={"tool_name": tc["name"], "call_id": tc["id"], "input": tc["input"]}
                ))

            try:
                output = await tool.execute(**tc["input"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": str(output),
                })
            except Exception as e:
                results.append(self._error_result(tc["id"], str(e)))

            if on_event:
                await on_event(ExecutionEvent(
                    type=EventType.TOOL_CALL_COMPLETED,
                    turn_index=turn_index,
                    timestamp=__import__("time").time(),
                    data={"tool_name": tc["name"], "call_id": tc["id"]}
                ))
        return results

    def _error_result(self, call_id: str, error: str) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": f"Error: {error}",
            "is_error": True,
        }
```

#### 4.3 模型客户端 `model/client.py`

**要求**：封装 API 调用，返回包含 `stop_reason` 的标准化响应。

```python
import httpx
import json
from typing import AsyncIterator, Callable, Awaitable, Optional
from dataclasses import dataclass
from ..harness.events import StopReason

@dataclass
class ModelResponse:
    content: Optional[str]
    tool_calls: Optional[list[dict]]
    stop_reason: StopReason
    usage: Optional[dict] = None

class ModelClient:
    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.base_url = base_url or "https://api.anthropic.com"
        self.model = model

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream: bool = True,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> ModelResponse:
        """
        发送请求，流式模式下解析 SSE 事件，聚合 text 和 tool_use。
        """
        # 实现细节：调用 Anthropic Messages API，处理流式响应
        # 必须正确映射 stop_reason：end_turn / tool_use / max_tokens / refusal
        ...
```

#### 4.4 Agent Loop `harness/loop.py`

**核心**：实现 `while` 循环，根据 `stop_reason` 分支处理。

```python
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional
from .events import ExecutionEvent, EventType, StopReason
from ..tools.router import ToolRouter
from ..model.client import ModelClient

@dataclass
class LoopConfig:
    max_turns: int = 20

class AgentLoop:
    def __init__(self, model_client: ModelClient, tool_router: ToolRouter, config: LoopConfig):
        self.model_client = model_client
        self.tool_router = tool_router
        self.config = config

    async def run(
        self,
        messages: list[dict],
        tools: list[dict],
        on_event: Optional[Callable[[ExecutionEvent], Awaitable[None]]] = None,
    ) -> tuple[str, list[ExecutionEvent]]:
        turn = 0
        events = []
        current_messages = messages.copy()

        while turn < self.config.max_turns:
            turn += 1
            # 调用模型
            response = await self.model_client.chat(
                messages=current_messages,
                tools=tools,
                stream=True,
                on_text_delta=lambda delta: self._emit_text_delta(delta, turn, on_event),
            )
            # 将 assistant 消息追加到历史
            current_messages.append(self._assistant_message(response))

            if response.stop_reason == StopReason.END_TURN:
                return response.content or "", events
            elif response.stop_reason == StopReason.TOOL_USE and response.tool_calls:
                tool_results = await self.tool_router.route(
                    response.tool_calls, turn, on_event
                )
                current_messages.append({"role": "user", "content": tool_results})
            else:
                # 处理异常终止
                return self._handle_error(response.stop_reason), events

        return "[Max turns exceeded]", events

    def _assistant_message(self, response: ModelResponse) -> dict:
        msg = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            msg["tool_calls"] = response.tool_calls
        return msg
```

#### 4.5 编排入口 `agent_runner.py`

**要求**：组装 Harness 组件，提供统一调用接口，并更新会话历史。

```python
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional
from core.types import TemplateContext, ReplyPayload
from core.config import AppConfig
from context.session_store import SessionStore
from .harness.loop import AgentLoop, LoopConfig
from .harness.events import ExecutionEvent
from .tools.registry import ToolRegistry
from .tools.router import ToolRouter
from .model.client import ModelClient

@dataclass
class AgentRunResult:
    payload: ReplyPayload
    events: list[ExecutionEvent]
    turns: int
    tool_calls_count: int

class AgentRunner:
    def __init__(
        self,
        config: AppConfig,
        session_store: SessionStore,
        tool_registry: Optional[ToolRegistry] = None,
    ):
        self.config = config
        self.session_store = session_store
        self.tool_registry = tool_registry or ToolRegistry()

        self.model_client = ModelClient(
            api_key=config.model.api_key,
            base_url=config.model.base_url,
            model=config.model.model,
        )
        self.tool_router = ToolRouter(self.tool_registry)
        self.loop = AgentLoop(
            model_client=self.model_client,
            tool_router=self.tool_router,
            config=LoopConfig(max_turns=config.max_turns),
        )

    async def run(
        self,
        context: TemplateContext,
        on_event: Optional[Callable[[ExecutionEvent], Awaitable[None]]] = None,
    ) -> AgentRunResult:
        # 构建消息历史
        messages = context.conversation_history.copy()
        if context.body:
            messages.append({"role": "user", "content": context.body})

        # 执行 Agent Loop
        final_text, events = await self.loop.run(
            messages=messages,
            tools=self.tool_registry.list_definitions(),
            on_event=on_event,
        )

        # 更新会话历史
        self.session_store.append_turn(
            context.session_key,
            user_msg=context.body,
            assistant_msg=final_text,
        )

        return AgentRunResult(
            payload=ReplyPayload(text=final_text),
            events=events,
            turns=len([e for e in events if e.type == EventType.TURN_COMPLETED]),
            tool_calls_count=len([e for e in events if e.type == EventType.TOOL_CALL_COMPLETED]),
        )
```

### 5. 与第一版兼容性要求

- 第一版的 `ModelExecutor.generate()` 方法应作为 `AgentRunner.run()` 的一个特例保留（当 `tools=[]` 且 `max_turns=1` 时行为一致）。
- 第一版的所有现有测试用例必须在第二版中**全部通过**（可能需要微调 mock 对象）。

### 6. 测试要求

| 测试文件 | 覆盖内容 |
|----------|----------|
| `tests/harness/test_loop.py` | 模拟不同 `stop_reason`，验证循环逻辑、最大轮次限制 |
| `tests/tools/test_router.py` | 工具路由正确性、异常处理、事件发出 |
| `tests/model/test_client.py` | 流式解析、错误映射、回退逻辑（可选） |
| `tests/test_agent_runner.py` | 端到端集成测试（使用 mock 模型客户端） |

### 7. 迭代步骤（供开发者参考）

1. **保持第一版可运行**：先创建 `harness/`、`tools/` 等目录，将原有 `model_executor.py` 重构为 `model/client.py`，确保原有测试通过。
2. **实现事件系统与工具注册**：编写 `events.py` 和 `registry.py`，单元测试覆盖。
3. **实现 AgentLoop**：编写循环逻辑，使用 mock `ModelClient` 进行测试。
4. **集成 AgentRunner**：替换原有 `ModelExecutor` 调用，更新 `agent_runner.py`。
5. **添加示例工具**：`calculator.py` 用于演示。
6. **更新文档与示例**：提供流式输出和工具调用的 demo。

### 8. 扩展预留点（注释标记）

在代码中以下位置添加 `# TODO: Extension point - ...` 注释：

- `model/client.py`：支持多模型回退（复用第一版 `_fallback.py`）。
- `harness/loop.py`：支持人类审批（工具调用前暂停）。
- `tools/registry.py`：支持动态加载外部工具（如 MCP 协议）。
- `agent_runner.py`：支持子 Agent 委派（将上下文传递给另一个 Agent）。

### 9. 验收标准

- [ ] 所有模块文件行数 ≤ 200 行（不含注释和空行）。
- [ ] 提供 `pytest` 测试套件，覆盖率 ≥ 80%。
- [ ] 能够通过 `ConsoleChannel` 交互式演示多轮工具调用（例如询问“计算 123 * 456”并触发计算器工具）。
- [ ] 流式输出逐字打印，工具调用过程实时显示。

---

**文档版本**：v2.0  
**基于**：Anthropic "Building effective agents" 指南及 Managed Agent 架构思想  
**预计编码时间**：约 4-6 小时（包含测试）