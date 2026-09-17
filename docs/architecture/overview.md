# Architecture Overview

HpAgent exposes Web and QQ interaction surfaces over one application and conversation model. Surface adapters normalize identity and input; neither surface owns a separate agent runtime.

```text
Web browser ──► FastAPI ─┐
                         ├─► CommandService ─► PostgreSQL transaction + Outbox
QQ provider ─► adapter ──┘                         │
                                                   ▼
                                           Outbox Dispatcher
                                                   │
                                                   ▼
                          Temporal AgentLifecycleWorkflow
                                                   │
                                                   ▼
                               AgentRunWorkflow + strategy
                                                   │
                              ┌────────────────────┼────────────────────┐
                              ▼                    ▼                    ▼
                        Context/Brain        Actions/Tools       Memory/Workspace
                              └────────────────────┼────────────────────┘
                                                   ▼
                                          committed PostgreSQL result
                                                   │
                                      ┌────────────┴────────────┐
                                      ▼                         ▼
                                  Web SSE                  QQ delivery
```

## Logical layers

- **Surface adapters** own HTTP, browser auth, SSE, QQ provider protocol, routing, and delivery formatting.
- **Application services** admit commands, assemble context, schedule work, retain memory, and coordinate delivery.
- **Domain packages** define conversation, file, research, and execution invariants without transport concerns.
- **Persistence** owns PostgreSQL transactions, repositories, migrations, projections, and Outbox records.
- **Durable orchestration** owns Temporal workflows, activities, retries, waiting, and lifecycle transitions.
- **Capabilities** provide Brain/model calls, Actions, MCP, memory, sandbox, workspace, file, research, artifact, and document behavior.

The worker composition root constructs shared infrastructure once and supplies it to workflow activity implementations. The Web API only admits and projects work. QQ ingress uses the same command boundary, while QQ delivery consumes committed results.

Related: [runtime](runtime.md), [state ownership](data-and-state.md), [reliability](reliability.md), and [sequences](sequences.md).
