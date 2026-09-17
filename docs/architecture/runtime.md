# 运行时

## 命令接收

Web 和 QQ Adapter 将请求转换为应用命令。`CommandService` 在同一个 PostgreSQL 事务中写入 Message、Session/Run、幂等事实和 Outbox Event。只有事务提交成功，命令才算被系统接收。

## 分发与生命周期

Outbox Dispatcher 租用待处理事件，并在 `hpagent-web-lifecycle` 队列启动 `AgentLifecycleWorkflow`。Lifecycle Workflow 准备 Run、记录 Temporal Identity、在 `hpagent-web-agent` 队列启动 `AgentRunWorkflow`，最终完成成功、失败或取消状态。

`AgentRunWorkflow` 根据请求选择执行策略：

- **ReAct**：交替执行模型决策和工具调用。
- **Plan-and-Execute**：先生成计划，再持久化执行各步骤。

Child Workflow 和 Activity 携带稳定的 Run 与 Operation Identity。所有非确定性 I/O 都在 Activity 中完成，包括数据库、模型、工具、Workspace、Memory、Research、Artifact 和 Document 操作。

## 共享运行时

主 Worker 的 Composition Root 拥有一份 Resource Pool、Workspace Isolation Runtime、Sandbox Manager、Brain、Action Runtime、Redis Client、可选 MCP Manager 和 Hindsight Memory 协作者。`Context` 将对话状态、记忆召回、文件、Workspace 信息、Prompt 和来源 Interaction Profile 组装后交给 Brain。

`Brain` 负责面向模型的决策；`Actions` 负责选择和调用本地工具、MCP 工具及 Sandbox 操作。可变的工具选择状态按 Resource 与 Execution Identity 隔离。

## 独立持久化能力

Research 和 Artifact Workflow 注册在同一 Durable Runtime 中，但它们是应用能力，不是 Agent Strategy。Heavy Document Normalization 在独立的 `hpagent-document-worker` 进程和 `hpagent-document` 队列执行，避免高开销转换占用主 Agent Activity 容量。

队列与 Workflow 名称见 [Temporal 参考](../reference/temporal.md)。
