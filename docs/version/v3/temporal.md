# Temporal 集成指南：为 HpAgent 添加持久执行与分布式弹性

> 本指南面向 HpAgent 项目开发者，说明如何在不破坏现有分层架构的前提下引入 Temporal，使自动回复框架具备生产级的持久执行、故障恢复和可观测能力。

---

## 1. 为什么要引入 Temporal

当前 HpAgent 的核心循环由 `Harness` 和 `Orchestrator` 驱动，任务状态保存在内存或外部文件中。一旦服务重启或遭遇长时间 LLM 调用失败，正在进行的对话任务会丢失，需要人工恢复。Temporal 通过以下特性弥补这些短板：

- **持久执行**：任务的状态、对话历史、中间结果全部持久化，服务崩溃后能毫秒级恢复。
- **可靠重试**：对 LLM 调用、工具调用等易失败操作，提供策略化的重试与超时控制。
- **长时间运行**：单次对话可能持续数小时，Temporal 为这类长任务专门设计。
- **可观测性**：内置 Web UI，实时查看每次对话的“思考→行动”过程。

---

## 2. 集成目标

- **最小侵入**：保留现有接口 (`IOrchestration`、`IHarness`、`ISession` 等)，内部实现切换到 Temporal。
- **双模式兼容**：允许通过配置选择“轻量模式（内存/文件）”或“Temporal 模式”，方便开发与生产切换。
- **逐步迁移**：可优先将长时间、高价值的对话任务迁移到 Temporal，短期快速对话仍使用原有路径。

---

## 3. 架构调整总览

引入 Temporal 后，系统分层演变为：

```
┌─────────────┐
│   Channels   │  (不变)
└──────┬──────┘
       │
┌──────▼──────────────────────────────────────────────────────┐
│              Orchestration (Temporal Workflow)              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ AgentWorkflow (替代原 Orchestrator + Harness 循环)     │ │
│  │  - 接收输入 → 事件循环 → 调用 Activities → 输出结果    │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────┬─────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌───────────────┐  ┌──────────────┐  ┌──────────────────┐
│ Session       │  │ Harness      │  │ Sandbox          │
│ (Activity)    │  │ (Activity)   │  │ (Activity)       │
│ - 事件读写    │  │ - LLM 调用   │  │ - 工具执行       │
└───────────────┘  └──────────────┘  └──────────────────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│          Temporal Service (调度 + 持久化事件历史)             │
└──────────────────────────────────────────────────────────────┘
```

**关键变化**：
- **Orchestrator** 变为 Temporal Workflow，其原本的状态机由 Temporal 的事件溯源机制接管。
- **Harness 的推理循环**被移入 Workflow 中（保持确定性），所有副作用调用变为 Activities。
- **SessionManager** 保留为 Activity，负责读写外部存储的事件日志（可选，也可完全依赖 Temporal 的历史）。
- **ResourcePool、SandboxManager** 等仍保持原有职责，但调用它们的代码必须包装在 Activity 内。

---

## 4. 核心映射关系

| HpAgent 组件 | Temporal 映射 | 说明 |
| :--- | :--- | :--- |
| `Orchestrator.process_task()` | **Workflow.run()** | 用确定性代码编排一次对话任务 |
| `Harness.wake()` → 上下文构建 → 模型调用 → 工具路由循环 | **Workflow 内部的 while 循环** + **Activities** | LLM 调用、工具调用成为 Activities |
| `SessionManager.emit_event()` / `get_events()` | **Activity** 或直接依赖 Temporal `workflow.logger` + 内置历史 | 可选择保留外部持久化或使用 Temporal 历史 |
| `ResourcePool.generate()` | **Activity `call_model`** | 封装模型 HTTP 请求，自动重试 |
| `Sandbox.execute_tool()` | **Activity `execute_tool`** | 工具执行可能变更外部状态，必须是 Activity |
| `TaskManager` 状态机 | Temporal 内置的状态机（事件历史） | 不再需要手动管理任务状态 |
| 事件流水 (`EventType`) | Temporal `workflow.info().get_current_history_length()` 或自定义查询 | 通过 Temporal 的任务历史回溯 |

---

## 5. 详细设计

### 5.1 Workflow: `AgentWorkflow`

它直接对应一个对话任务，从接收用户消息到返回最终回复，内部循环与原有 Harness 一致。

```python
from temporalio import workflow
from datetime import timedelta

@workflow.defn
class AgentWorkflow:
    @workflow.run
    async def run(self, session_id: str, user_message: str) -> str:
        # 初始化对话历史（状态自动持久化）
        history = [{"role": "user", "content": user_message}]
        tools = await workflow.execute_activity(
            list_tools, session_id, start_to_close_timeout=timedelta(seconds=5))

        while True:
            # 1. 调用 LLM（Activity）
            model_resp = await workflow.execute_activity(
                call_model,
                args=[session_id, history, tools],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3)
            )

            # 2. 检查是否需要工具调用
            if model_resp.tool_calls:
                # 执行每一个工具（Activity）
                for tc in model_resp.tool_calls:
                    tool_result = await workflow.execute_activity(
                        execute_tool,
                        args=[session_id, tc.name, tc.arguments],
                        start_to_close_timeout=timedelta(seconds=30)
                    )
                    # 将结果加入历史
                    history.append({"role": "tool", ...})
                # 继续循环，让 LLM 根据工具结果重新生成回复
                continue
            else:
                # 3. 得到最终回复，存储事件（可选）
                await workflow.execute_activity(
                    record_event,
                    args=[session_id, EventType.MODEL_MESSAGE, model_resp.content],
                    start_to_close_timeout=timedelta(seconds=5)
                )
                return model_resp.content
```

### 5.2 Activities（活动定义）

现有代码中的 `ModelClient.generate()`, `ToolRegistry.execute()`, `SessionManager.emit_event()` 等需要封装成 Activity 函数。

```python
@activity.defn
async def call_model(session_id: str, messages: list, tools: list) -> ModelResponse:
    # 复用 ResourcePool 和 ModelClient 的逻辑
    pool = get_resource_pool()   # 依赖注入或全局单例
    return await pool.generate(session_id, messages, tools)

@activity.defn
async def execute_tool(session_id: str, tool_name: str, arguments: dict) -> str:
    # 复用 SandboxManager
    sandbox = await SandboxManager.get(session_id)
    return await sandbox.execute(tool_name, arguments)

@activity.defn
async def list_tools(session_id: str) -> list[dict]:
    # 获取可用工具的定义
    sandbox = await SandboxManager.get(session_id)
    return sandbox.list_tool_definitions()
```

### 5.3 Worker 配置

Worker 进程负责执行 Workflow 和 Activity 代码，需要注册所有相关类/函数。

```python
from temporalio.client import Client
from temporalio.worker import Worker

async def main():
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="hpagent-task-queue",
        workflows=[AgentWorkflow],
        activities=[call_model, execute_tool, list_tools, record_event]
    )
    await worker.run()
```

### 5.4 启动工作流（替代原 Orchestrator 调用）

```python
# 原: orchestrator.process_task(session_id, user_message)
# 现:
result = await client.execute_workflow(
    AgentWorkflow.run,
    args=[session_id, user_message],
    id=f"agent-{session_id}-{message_id}",  # 唯一ID，用于去重
    task_queue="hpagent-task-queue"
)
```

---

## 6. 保留现有接口的适配器模式

若希望保持 `IOrchestration` 接口不变，可创建一个 `TemporalOrchestrator` 适配器：

```python
class TemporalOrchestrator(IOrchestration):
    async def process_task(self, session_id: str, user_message: str) -> str:
        return await self.temporal_client.execute_workflow(...)
```

这样上层调用者（如 `ConsoleChannel`）无需改动任何代码，只需注入 `TemporalOrchestrator` 即可。

---

## 7. 事件持久化策略

原有 `SessionManager` 将事件记录到文件中。引入 Temporal 后有两种方式：

1. **双重记录**：Activity `record_event` 仍然写入文件，保证查询兼容性。
2. **完全依赖 Temporal**：利用 Temporal 的 `get_workflow_history` API 获取所有过去事件，不再维护文件存储。推荐方式，但需改造查询接口。

我们推荐暂时保留双重记录，待稳定后再逐步切换。

---

## 8. 迁移路线图

**第一阶段：添加依赖与 Worker**
- 安装 `temporalio`，启动开发服务器 `temporal server start-dev`。
- 编写 Worker 脚本，注册现有 Harness 相关函数为 Activities。
- 保证能正常拉取任务。

**第二阶段：实现 TemporalOrchestrator 并双轨运行**
- 创建 `AgentWorkflow`，内部调用 Activities。
- 实现 `TemporalOrchestrator`，通过配置开关控制使用原版还是 Temporal 版。
- 在测试环境运行，对比结果。

**第三阶段：逐步下线原 TaskManager 状态机**
- 观察 Temporal Web UI 中的工作流历史，确认状态恢复正确。
- 逐步将手动重试、超时逻辑移除，转由 Temporal 配置。
- 最终移除原 TaskManager 的状态跟踪代码。

---

## 9. 注意事项

- **确定性要求**：Workflow 中不能直接调用 `random()`、`time.now()` 或直接 HTTP 请求，必须通过 Activity。
- **Activity 幂等性**：Temporal 可能重试 Activity，需保证工具执行（如发邮件）具有幂等或去重控制。
- **超时与重试**：为每个 Activity 设置 `start_to_close_timeout` 和 `retry_policy`，避免 LLM 卡住整个工作流。
- **任务队列隔离**：建议为不同环境（开发/生产）使用不同 Task Queue。
- **Sandbox 生命周期**：Sandbox 的创建/销毁推荐作为独立的 Activity，并确保在工作流结束时清理。

---

## 10. 总结

通过将 `Orchestrator` 和 `Harness` 的核心循环重构为 Temporal Workflow + Activities，HpAgent 可在不改动外部接口的条件下获得持久执行、自动重试和完整的执行历史追踪。整个集成过程对现有分层架构伤害极小，且允许渐进式迁移，是面向生产环境的自然演进方案。

接下来，你可以按照**迁移路线图**，先从最小的“模型调用 Activity”开始试验，逐步将整个对话循环交给 Temporal。如有疑问，可参考本文档开头的 Temporal 核心概念解释，或在本项目的 `docs/` 目录下查看后续详细示例代码。