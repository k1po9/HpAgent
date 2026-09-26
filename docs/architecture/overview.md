# 架构总览

HpAgent 通过统一的应用与对话模型同时提供 Web 和 QQ 交互入口。Surface Adapter 负责标准化身份与输入，但不拥有独立的 Agent Runtime。

```text
Web Browser ─► FastAPI ─┐
                        ├─► CommandService ─► PostgreSQL Transaction + Outbox
QQ Provider ─► Adapter ─┘                         │
                                                  ▼
                                          Outbox Dispatcher
                                                  │
                                                  ▼
                         Temporal AgentLifecycleWorkflow
                                                  │
                                                  ▼
                              AgentRunWorkflow + Strategy
                                                  │
                             ┌────────────────────┼────────────────────┐
                             ▼                    ▼                    ▼
                       Context/Brain        Actions/Tools       Memory/Workspace
                             └────────────────────┼────────────────────┘
                                                  ▼
                                         PostgreSQL Committed Result
                                                  │
                                     ┌────────────┴────────────┐
                                     ▼                         ▼
                                 Web SSE                  QQ Delivery
```

## 逻辑分层

- **Surface Adapter**：负责 HTTP、浏览器认证、SSE、QQ Provider 协议、路由和投递格式。
- **Application Service**：负责命令接收、上下文组装、任务调度、记忆保留和消息投递协调。
- **Domain Package**：定义 Conversation、File、Research 和 Execution 规则，不依赖传输协议。
- **Persistence**：负责 PostgreSQL 事务、Repository、Migration、Projection 和 Outbox。
- **Durable Orchestration**：负责 Temporal Workflow、Activity、重试、等待和生命周期状态转换。
- **Capability**：提供 Brain/Model、Actions、MCP、Memory、Sandbox、Workspace、File、Research、Artifact 和 Document 能力。

Worker Composition Root 只构造一份共享基础设施，并注入 Workflow Activity。Web API 只负责接收命令和查询投影；QQ Ingress 复用同一命令边界，QQ Delivery 只消费已提交结果。

延伸阅读：[运行时](runtime.md)、[状态归属](data-and-state.md)、[Workspace v4.1](workspace-v4.1.md)、[可靠性](reliability.md)和[关键时序](sequences.md)。
