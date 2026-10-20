# 架构总览

HpAgent 通过统一 Account 和命令边界提供 Web / QQ 入口。Main Agent 面向交互，Work 保存持续委托，Run 是有限执行，Execution 是独立执行上下文；Surface Adapter 不拥有另一套 Agent Runtime。

```text
Web Browser / QQ Provider
          ↓
Application / Conversation commands / Work commands
          ↓
PostgreSQL：Message、Work、requirement、wakeup、Run + Outbox
          ↓
Outbox Dispatcher → AgentLifecycleWorkflow
          ↓
StrategyRegistry：deterministic / fixed_workflow / generic_agent
          ↓
Execution：Context / Model / Actions / Memory / Workspace
          ↓
Run 终态 / 成果版本 / 保存事实 / WorkCompletionPolicy
          ↓
Web 查询与 SSE / notifications → deliveries → Web inbox / QQ
```

## 逻辑分层

- **Surface Adapter**：HTTP、认证、SSE、QQ 协议、路由和投递格式；前端 AI / 空间 / 任务及上下文 Inspector 是同一 API 的操作入口。
- **Application Service**：上下文组装、Main 委托工具、入口协调、记忆保留和渠道 Adapter。
- **Domain Package**：Conversation、Work、Run、File、Research 等领域规则；交互、委托、执行、验收与投递分别记账。
- **Persistence**：PostgreSQL 事务、Repository、Migration、Projection、Outbox、预算与容量事实。
- **Durable Orchestration**：一个有限 Run Lifecycle，注册策略、Child Workflow、Activity、等待和终态。Work 到期调度是 PostgreSQL evaluator。
- **Capability**：Model、Actions、MCP、Memory、Sandbox、Workspace、Research、Artifact 和 Document。

主 Worker Composition Root 构造共享基础设施并注入 Activity；可变执行上下文按 Execution 隔离。API 接收命令和查询投影，QQ Ingress 复用命令，Delivery 只消费已提交通知。Heavy Document Worker 是独立资源进程边界，不形成另一套委托或文件系统。

Conversation 保留同对话单 active chat Run；Work Run 不占聊天槽、不伪造 Message、不强制依赖 Session / Git。单 Work 协调权、Execution attempt lease、资源锁和跨进程容量票据是四项独立约束。

当前目录存在跨领域调用，并未形成严格依赖隔离；新增改动要沿实际事务和调用链检查，不能根据目录名假定边界已经闭合。

按任务找入口见[代码地图](../reference/repository-layout.md)。继续阅读：[执行、状态与恢复](execution.md)、[Work](work.md)、[Workspace](workspace.md)、[能力边界](capabilities.md)、[Web 契约](web-ui.md)。操作见[功能指南](../operations/web-workbench.md)。
