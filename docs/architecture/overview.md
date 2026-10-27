# 架构总览

HpAgent 通过统一 Account 和命令边界提供 Web / QQ 入口。Main Agent 面向交互，Work 保存持续委托，Run 是有限执行，Execution 是独立执行上下文；Surface Adapter 不拥有另一套 Agent Runtime。

```text
Web Browser / QQ（SnowLuma → OneBot11 反向 WebSocket）
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

## QQ 接入与回执

业务渠道名为 `onebot11`，SnowLuma 2.2.0 是 QQ 协议实现，作为可选 Compose 服务运行。
SnowLuma 主动连接 Worker 的内部 `ws://hpagent:8082/onebot/v11`；Worker 校验 Bearer、
唯一 bot 身份和 Universal role，再核验实际登录账号、协议版本及 online/good。
端口不发布到宿主；管理页面只绑定宿主回环。QQ readiness 与 Worker/Web liveness 分开。

接收循环独立处理动作应答，业务消息经有界队列进入规范化、身份绑定、群聊触发与
Conversation 命令。稳定的 bot/会话路由和外部消息 ID 决定幂等键；单连接代次隔离旧连接事件与迟到应答。
反向 WebSocket 不提供持久入站队列，断线期间不能承诺补发。

出站由独立 Delivery 消费 PostgreSQL 已提交的通知，以消息段数组发送，并保存逐片真实 QQ
message_id。控制回复也等待匹配连接的动作回执。外发后结果未知或发送者丢失时保留 `uncertain`，
经显式决议恢复，不能重跑内容 Run 或盲重发。Run 成功、Work 完成、渠道 accepted 与用户验收分别记账。
图片只记录可下载来源；来源可读取不等于视觉模型已理解图片。

Conversation 保留同对话单 active chat Run；Work Run 不占聊天槽、不伪造 Message、不强制依赖 Session / Git。单 Work 协调权、Execution attempt lease、资源锁和跨进程容量票据是四项独立约束。

当前目录存在跨领域调用，并未形成严格依赖隔离；新增改动要沿实际事务和调用链检查，不能根据目录名假定边界已经闭合。

按任务找入口见[代码地图](../reference/repository-layout.md)。继续阅读：[执行、状态与恢复](execution.md)、[Work](work.md)、[Workspace](workspace.md)、[能力边界](capabilities.md)、[Web 契约](web-ui.md)。操作见[功能指南](../operations/web-workbench.md)。
