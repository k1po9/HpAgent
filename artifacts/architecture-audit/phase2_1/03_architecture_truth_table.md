# 分层架构真相表

| Package | 职责 | 逻辑 owner | 能力 | Roots | 文件分类分布 | 置信度 |
| --- | --- | --- | --- | --- | --- | --- |
| src/account | 统一身份解析、注册、绑定与凭证；JSON 服务单独保留 | Identity & credentials | P2 | R1;R2;R5 | UNKNOWN:3, OPS_ADMIN:1, PROD_CONDITIONAL:6 | 见文件行；MEDIUM |
| src/actions | 选择并执行工具，汇总工具结果 | Agent tool execution | P3 | R1;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:1 | 见文件行；MEDIUM |
| src/agent | 生产 Brain/Action DTO 与被拒绝的多 Agent 编排同包 | Mixed protocol / experimental Multi-Agent | MULTIPLE | R1;R4;R5 | UNKNOWN:1, EXPERIMENTAL_REJECTED:13, PROD_CONDITIONAL:1 | 见文件行；MEDIUM |
| src/agent_activities | 模型/工具/上下文 Activity、lease/transcript/operation 与覆盖审批 | Durable Agent data plane | MULTIPLE | R1;R3;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:4 | 见文件行；MEDIUM |
| src/agent_execution | QQ/Web Host、Facade、legacy loop、预算及 Trace | Agent execution hosts & tracing | MULTIPLE | R1;R2;R3;R4;R5;R6 | UNKNOWN:2, PROD_CONDITIONAL:16 | 见文件行；MEDIUM |
| src/agent_workflows | ReAct/Plan-and-Execute/step/tool 的可恢复 Workflow | Durable Agent control flow | MULTIPLE | R1;R2;R3;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:6 | 见文件行；MEDIUM |
| src/application | Ingress、Conversation、Reply、Context、Archive、Memory、Metrics | Application use cases | MULTIPLE | R1;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:10 | 见文件行；MEDIUM |
| src/bootstrap | 构造 QQ 服务并组合共享 Brain/Action/Facade | Runtime composition | MULTIPLE | R1;R5 | UNKNOWN:1, PROD_CONDITIONAL:1 | 见文件行；MEDIUM |
| src/brain | 模型决策、查询改写与上下文驱动推理 | Model decisions | P3 | R1;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:1 | 见文件行；MEDIUM |
| src/channels | NapCat/OfficialQQ 接入与路由；Console 非当前 factory 支持项 | QQ transport | P1 | R1;R5 | UNKNOWN:3, PROD_CONDITIONAL:3 | 见文件行；MEDIUM |
| src/common | 共享 DTO、错误、日志、token 与模型用量 | Shared contracts / telemetry | MULTIPLE | R1;R2;R3;R4;R5 | UNKNOWN:2, PROD_CONDITIONAL:5 | 见文件行；MEDIUM |
| src/document_activities | 独立队列上的重型规范化、幂等落库和预算 | Heavy document execution | P5 | R6 | UNKNOWN:2, PROD_CONDITIONAL:1 | 见文件行；MEDIUM |
| src/file_adapters | 格式读取、Office 输出、Gotenberg 转换、Docling/MarkItDown | Document format providers | P5 | R1;R4;R5;R6 | UNKNOWN:1, PROD_CONDITIONAL:8 | 见文件行；MEDIUM |
| src/file_domain | 值对象、provider 协议、规范化结果、审批和持久版本 | File contracts & persistence | P5 | R1;R2;R3;R4;R5;R6 | UNKNOWN:2, PROD_CONDITIONAL:5 | 见文件行；MEDIUM |
| src/file_runtime | 解析 Run 文件、选择 adapter、输出发布及 Research Markdown | File routing & publication | MULTIPLE | R1;R3;R4;R5;R6 | UNKNOWN:1, PROD_CONDITIONAL:5 | 见文件行；MEDIUM |
| src/harness | QQ Temporal Activities、上下文消息组装和 Prompt 配置适配 | QQ activities / prompt context | MULTIPLE | R1;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:3 | 见文件行；MEDIUM |
| src/memory | Hindsight HTTP 客户端和 Redis 群短期上下文 | Long-term / group memory adapters | P2 | R1;R5 | UNKNOWN:1, PROD_CONDITIONAL:2 | 见文件行；MEDIUM |
| src/orchestration | 主进程组合、Workflow、队列、Outbox、恢复、调度和关闭 | Process lifecycle & Temporal | MULTIPLE | R1;R3;R4;R5;R6 | UNKNOWN:2, PROD_CONDITIONAL:20 | 见文件行；MEDIUM |
| src/persistence | Repository/UoW/migration runner，不拥有 SQL migration 文件 | PostgreSQL runtime | MULTIPLE | R1;R2;R3;R4;R5;R6;R8 | UNKNOWN:1, OPS_ADMIN:1, PROD_CONDITIONAL:2 | 见文件行；MEDIUM |
| src/research_activities | 固定阶段 Activity：发现、抓取、证据、综合、核验、发布 | Research execution | P6 | R1;R3;R5 | UNKNOWN:1, PROD_CONDITIONAL:1 | 见文件行；MEDIUM |
| src/research_adapters | SearXNG/GitHub/RSS、静态抓取、浏览器回退、模型综合 | Research external providers | P6 | R1;R3;R5 | UNKNOWN:1, PROD_CONDITIONAL:3 | 见文件行；MEDIUM |
| src/research_domain | 任务合同、命令事务、预算、Outbox 及研究数据 Repository | Research commands & persistence | P6 | R1;R2;R3;R5 | UNKNOWN:1, PROD_CONDITIONAL:4 | 见文件行；MEDIUM |
| src/resources | 模型链、凭证、回退、embedding、rerank 与预算上下文 | Model / retrieval infrastructure | P3 | R1;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:5 | 见文件行；MEDIUM |
| src/sandbox | Sandbox 生命周期、本地/MCP/Skill 工具、检索和执行隔离 | Tool runtime / workspace execution | P3 | R1;R4;R5 | UNKNOWN:7, PROD_CONDITIONAL:28 | 见文件行；MEDIUM |
| src/session | QQ Redis/WAL/归档状态；SQLite workspace 元数据与分支准备 | QQ state / workspace metadata | MULTIPLE | R1;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:4 | 见文件行；MEDIUM |
| src/storage | Redis 缓存、LocalFileStore 和 tenant 不可变文件对象 | Storage adapters | MULTIPLE | R1;R2;R3;R4;R5;R6 | UNKNOWN:2, PROD_CONDITIONAL:3 | 见文件行；MEDIUM |
| src/web_api | 认证、命令查询路由、SSE 和终态发布；fake executor 有开发门禁 | Web HTTP / authentication / SSE | MULTIPLE | R2 | UNKNOWN:1, PROD_DIRECT:2, PROD_CONDITIONAL:7, DEV_ONLY:1 | 见文件行；MEDIUM |
| src/web_artifacts | 版本命令、Artifact outbox、build 与 HTML 生成 | Artifact versions | S1 | R1;R2;R3;R5 | UNKNOWN:1, PROD_CONDITIONAL:5 | 见文件行；MEDIUM |
| src/web_domain | Conversation/Message/Run、生命周期、Outbox、会话及文件服务 | Web transactional domain | MULTIPLE | R1;R2;R3;R4;R5 | UNKNOWN:1, PROD_CONDITIONAL:10 | 见文件行；MEDIUM |
| src/workspace | 账户锁、进程锁、Session 恢复、Run 文件范围及文件开关 | Workspace isolation / run file scope | MULTIPLE | R1;R3;R4;R5;R6 | UNKNOWN:1, PROD_CONDITIONAL:3 | 见文件行；MEDIUM |

## Production composition roots

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/main.py | HpAgent —— 主入口，负责加载配置并启动智能体服务。 | Repository / operations / NONE | R1 | PROD_DIRECT | src/common/logging.py;src/orchestration/config.py;src/orchestration/worker.py |  |  | HIGH |
| src/bootstrap/qq.py | QQ surface and shared Agent execution composition. | Runtime composition / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/account/postgres_account_service.py;src/account/validation.py;src/actions/runtime.py;src/agent_execution/brain_action_loop.py | src/orchestration/worker.py |  | MEDIUM |
| src/orchestration/worker.py | 组合共享基础设施、QQ、条件 Web、channels、scheduler、cleanup、Schedules 与 shutdown | Process composition & lifecycle / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/account/identity_binding_service.py;src/agent_activities/persistent_overwrite.py;src/agent_activities/runtime.py;src/agent_activities/store.py | src/main.py;src/orchestration/__init__.py;src/orchestration/web_worker.py | HIGH_FAN_OUT;LARGE_HOTSPOT | MEDIUM |
| src/orchestration/web_workers.py | Composition helpers for the isolated Web Temporal workers. | Process lifecycle & Temporal / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/account/validation.py;src/agent_workflows/agent_run.py;src/agent_workflows/agent_step.py;src/agent_workflows/contracts.py | src/orchestration/web_worker.py;src/orchestration/worker.py | HIGH_FAN_OUT;HISTORY_COMPATIBILITY;MIGRATION_GATE | MEDIUM |
| src/web_api/app.py | 定义 BodyLimitMiddleware;BodyTooLarge;CommonHeadersMiddleware;CsrfInvalid;EmptyMessage;InvalidIdempotencyKey;MessageTooLarge;ProtocolMiddleware;Unauthenticated;_error;_etag | Web HTTP / authentication / SSE / MULTIPLE | R2 | PROD_DIRECT | src/account/credentials.py;src/account/identity_binding_service.py;src/account/registration_service.py;src/agent_execution/tracing/repository.py | src/web_api/__init__.py;src/web_api/__main__.py | HIGH_FAN_OUT;LARGE_HOTSPOT | HIGH |

## Agent runtime

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/agent/protocol.py | 定义 BrainDecision、ActionRequest、ActionResult，连接当前 Brain/Action 与 Durable Activity | Agent runtime contracts / P3 | R1;R4;R5 | PROD_CONDITIONAL |  | src/actions/runtime.py;src/agent_activities/runtime.py;src/brain/engine.py | NAME_RESPONSIBILITY_DRIFT | MEDIUM |
| src/agent_execution/facade.py | Pure execution boundary shared by QQ and Web hosts (D-04). | Agent execution hosts & tracing / MULTIPLE | R1;R4;R5 | PROD_CONDITIONAL |  | src/agent_activities/runtime.py;src/agent_execution/audit.py;src/agent_execution/brain_action_loop.py | HIGH_FAN_IN | MEDIUM |
| src/agent_execution/brain_action_loop.py | Channel-neutral Brain/Action tool loop used by AgentExecutionFacade. | Agent execution hosts & tracing / MULTIPLE | R1;R4;R5 | PROD_CONDITIONAL | src/actions/runtime.py;src/agent_execution/facade.py;src/agent_execution/tracing/__init__.py;src/brain/engine.py | src/bootstrap/qq.py;src/orchestration/worker.py | LARGE_HOTSPOT | MEDIUM |
| src/agent_workflows/agent_run.py | Stable Agent strategy router workflow. | Durable Agent control flow / MULTIPLE | R1;R3;R4;R5 | PROD_CONDITIONAL | src/agent_workflows/contracts.py;src/agent_workflows/plan_execute.py;src/agent_workflows/react.py | src/orchestration/durable_web_workflow.py;src/orchestration/web_workers.py |  | MEDIUM |
| src/agent_activities/runtime.py | Model/tool/context capability Activities for durable Agent workflows. | Durable Agent data plane / MULTIPLE | R1;R3;R4;R5 | PROD_CONDITIONAL | src/agent/protocol.py;src/agent_activities/side_effects.py;src/agent_activities/store.py;src/agent_execution/facade.py | src/agent_activities/__init__.py;src/orchestration/worker.py | HIGH_FAN_OUT;LARGE_HOTSPOT | MEDIUM |

## QQ surface

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/channels/napcat.py | NapCatChannel —— NapCat QQ 协议通道，通过 WebSocket 连接 OneBot v11 客户端。 | QQ transport / P1 | R1 | PROD_CONDITIONAL | src/channels/base.py;src/common/types.py | src/channels/__init__.py;src/orchestration/worker.py |  | MEDIUM |
| src/channels/official_qq.py | OfficialQQChannel —— 官方 QQ 机器人渠道，通过 WebSocket 连接 QQ Bot API v2。 | QQ transport / P1 | R1 | PROD_CONDITIONAL | src/channels/base.py;src/common/types.py | src/channels/__init__.py;src/orchestration/worker.py | LARGE_HOTSPOT | MEDIUM |
| src/application/ingress.py | MessageIngressService —— 入站消息应用服务。 | Application use cases / MULTIPLE | R1 | PROD_CONDITIONAL | src/account/postgres_account_service.py;src/application/conversation.py;src/common/types.py | src/orchestration/worker.py |  | MEDIUM |
| src/application/conversation.py | ConversationService —— 会话启动、复用和本地资源准备。 | Application use cases / MULTIPLE | R1 | PROD_CONDITIONAL | src/common/types.py;src/session/workspace.py | src/application/ingress.py;src/orchestration/worker.py |  | MEDIUM |
| src/harness/activities.py | QQ turn/archive 与定时 reflection/metrics 的 Activity 适配及服务注入 | QQ Temporal application adapter / MULTIPLE | R1 | PROD_CONDITIONAL |  | src/orchestration/worker.py |  | MEDIUM |
| src/agent_execution/qq_host.py | QQ compatibility Host for the channel-neutral execution Facade (D-05). | Agent execution hosts & tracing / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/agent_execution/facade.py;src/common/logging.py;src/common/types.py | src/bootstrap/qq.py |  | MEDIUM |

## Web surface / API

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/web_api/app.py | 定义 BodyLimitMiddleware;BodyTooLarge;CommonHeadersMiddleware;CsrfInvalid;EmptyMessage;InvalidIdempotencyKey;MessageTooLarge;ProtocolMiddleware;Unauthenticated;_error;_etag | Web HTTP / authentication / SSE / MULTIPLE | R2 | PROD_DIRECT | src/account/credentials.py;src/account/identity_binding_service.py;src/account/registration_service.py;src/agent_execution/tracing/repository.py | src/web_api/__init__.py;src/web_api/__main__.py | HIGH_FAN_OUT;LARGE_HOTSPOT | HIGH |
| src/web_api/queries.py | 定义 QueryService;conversation_dto;file_dto;message_dto;run_dto;timestamp;trace_tree_dto | Web HTTP / authentication / SSE / MULTIPLE | R2 | PROD_CONDITIONAL | src/agent_execution/tracing/models.py;src/persistence/uow.py;src/web_api/security.py;src/web_domain/errors.py | src/web_api/app.py;src/web_api/sse.py |  | MEDIUM |
| src/web_api/auth.py | 定义 AuthContext;AuthService;ConfiguredPasswordCredentialAdapter;CredentialAdapter | Web HTTP / authentication / SSE / MULTIPLE | R2 | PROD_CONDITIONAL | src/account/identity.py;src/persistence/uow.py;src/web_api/config.py;src/web_api/security.py | src/web_api/app.py |  | MEDIUM |
| src/web_api/sse.py | 建立权威 Run snapshot 后转发在线事件，缓冲握手并处理降级 | Web SSE transport / S2 | R2 | PROD_CONDITIONAL | src/common/logging.py;src/persistence/uow.py;src/web_api/config.py;src/web_api/queries.py | src/web_api/app.py;src/web_api/terminal_publisher.py |  | MEDIUM |
| src/web_domain/services.py | Phase-A transaction protocols. UUIDs are generated by the application, not SQL. | Web transactional domain / MULTIPLE | R1;R2;R3;R5 | PROD_CONDITIONAL | src/agent_workflows/tool_execution.py;src/common/logging.py;src/persistence/repositories.py;src/persistence/uow.py | src/file_domain/approvals.py;src/research_activities/runtime.py;src/web_api/app.py | HIGH_FAN_IN;LARGE_HOTSPOT | MEDIUM |
| src/web_domain/lifecycle.py | Web-only adapter around the authoritative Run lifecycle transactions. | Web transactional domain / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/persistence/uow.py;src/web_domain/errors.py;src/web_domain/services.py | src/agent_execution/web_adapters.py;src/orchestration/web_activities.py;src/orchestration/web_reconcile_adapters.py |  | MEDIUM |

## Durable execution

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/orchestration/web_dispatcher.py | Outbox-to-Temporal dispatcher boundary for Web runs (D-03). | Process lifecycle & Temporal / MULTIPLE | R1;R3;R5 | PROD_CONDITIONAL | src/agent_workflows/contracts.py;src/agent_workflows/tool_execution.py;src/common/logging.py;src/orchestration/durable_web_workflow.py | src/orchestration/web_worker.py;src/orchestration/worker.py;src/web_domain/workflow_execution.py | HIGH_FAN_OUT | MEDIUM |
| src/orchestration/web_workflow.py | Deterministic Temporal workflow contract for one Web domain Run. | Process lifecycle & Temporal / MULTIPLE | R1;R3;R4;R5 | PROD_CONDITIONAL |  | src/file_runtime/routing.py;src/orchestration/artifact_workflow.py;src/orchestration/durable_web_workflow.py | HIGH_FAN_IN;HISTORY_COMPATIBILITY;MIGRATION_GATE | MEDIUM |
| src/orchestration/durable_web_workflow.py | Web business lifecycle using durable child Agent workflows. | Process lifecycle & Temporal / MULTIPLE | R1;R3;R5 | PROD_CONDITIONAL | src/agent_workflows/agent_run.py;src/agent_workflows/contracts.py;src/orchestration/web_workflow.py | src/orchestration/web_activities.py;src/orchestration/web_dispatcher.py;src/orchestration/web_workers.py | ARCHITECTURE_DRIFT;HISTORY_COMPATIBILITY;MIGRATION_GATE | MEDIUM |
| src/agent_activities/store.py | PostgreSQL durable data plane for Agent workflows. | Durable Agent data plane / MULTIPLE | R1;R3;R4;R5 | PROD_CONDITIONAL | src/persistence/uow.py | src/agent_activities/__init__.py;src/agent_activities/persistent_overwrite.py;src/agent_activities/runtime.py |  | MEDIUM |
| src/orchestration/web_reconciler.py | Reconcile domain Run truth with Temporal facts without holding DB locks over RPC. | Process lifecycle & Temporal / MULTIPLE | R1;R5 | PROD_CONDITIONAL |  | src/orchestration/worker.py;src/web_domain/workflow_execution.py |  | MEDIUM |
| src/agent_activities/side_effects.py | Recovery contracts for tool side effects executed by Activities. | Durable Agent data plane / MULTIPLE | R1;R4;R5 | PROD_CONDITIONAL |  | src/agent_activities/persistent_overwrite.py;src/agent_activities/runtime.py |  | MEDIUM |

## File / document

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/web_domain/file_services.py | Ownership-scoped upload lifecycle for FILE-P0-03. | Web transactional domain / MULTIPLE | R2 | PROD_CONDITIONAL | src/persistence/repositories.py;src/persistence/uow.py;src/storage/tenant_file_store.py;src/web_domain/errors.py | src/web_api/app.py |  | MEDIUM |
| src/file_runtime/resolver.py | Resolve logical Run inputs without accepting arbitrary host paths. | File routing & publication / MULTIPLE | R6 | PROD_CONDITIONAL | src/file_domain/models.py;src/workspace/file_scope.py | src/file_runtime/__init__.py |  | MEDIUM |
| src/file_runtime/routing.py | Deterministic routing from ordinary file reads to document normalization. | File routing & publication / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/document_activities/contracts.py;src/file_domain/models.py;src/orchestration/document_workflow.py;src/orchestration/web_workflow.py | src/orchestration/worker.py |  | MEDIUM |
| src/workspace/file_scope.py | 从 PostgreSQL 绑定输入文件并构造隔离 Run 文件执行范围 | Run file execution scope / P5 | R1;R4;R5;R6 | PROD_CONDITIONAL | src/persistence/uow.py;src/storage/tenant_file_store.py | src/file_domain/persistent.py;src/file_runtime/output.py;src/file_runtime/research_output.py | HIGH_FAN_IN | MEDIUM |
| src/file_domain/persistent.py | 账户 logical path、目的地状态、不可变修订和发布/覆盖服务 | Persistent file revision domain and SQL / P5 | R1;R2;R4;R5 | PROD_CONDITIONAL | src/file_domain/approvals.py;src/file_runtime/output.py;src/persistence/uow.py;src/storage/tenant_file_store.py | src/agent_activities/persistent_overwrite.py;src/orchestration/worker.py;src/web_api/app.py | PACKAGE_BOUNDARY_CROSSING | MEDIUM |
| src/file_domain/approvals.py | Durable approval grants for high-risk file actions. | File contracts & persistence / P5 | R1;R2;R3;R4;R5 | PROD_CONDITIONAL | src/agent_workflows/tool_execution.py;src/persistence/repositories.py;src/persistence/uow.py;src/web_domain/errors.py | src/file_domain/persistent.py;src/orchestration/worker.py;src/web_api/app.py |  | MEDIUM |
| src/orchestration/document_worker.py | 独立进程构造 Docling/document workspace 并仅注册低并发 normalize_document Activity | Heavy document process composition / P5 | R6 | PROD_CONDITIONAL | src/document_activities/__init__.py;src/file_adapters/__init__.py;src/orchestration/document_contracts.py;src/storage/tenant_file_store.py |  | ARCHITECTURE_DRIFT | MEDIUM |

## Research

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/research_domain/services.py | Transactional Task commands for Research Runs. | Research commands & persistence / P6 | R1;R2;R3;R5 | PROD_CONDITIONAL | src/persistence/repositories.py;src/persistence/uow.py;src/research_domain/models.py;src/research_domain/persistence.py | src/research_activities/runtime.py;src/web_api/app.py |  | MEDIUM |
| src/research_domain/persistence.py | 研究阶段数据 SQL、证据与引用、报告及 publish_research_artifact 函数调用 | Research PostgreSQL repositories / P6 | R1;R2;R3;R5 | PROD_CONDITIONAL | src/persistence/uow.py;src/research_domain/models.py | src/research_activities/runtime.py;src/research_domain/services.py | PACKAGE_BOUNDARY_CROSSING | MEDIUM |
| src/research_activities/runtime.py | Persisted, idempotent Activities for bounded iterative Research. | Research execution / P6 | R1;R3;R5 | PROD_CONDITIONAL | src/agent_execution/model_budget_context.py;src/agent_execution/run_budget.py;src/agent_execution/tracing/repository.py;src/orchestration/research_workflow.py | src/research_activities/__init__.py | HIGH_FAN_OUT;LARGE_HOTSPOT | MEDIUM |
| src/research_adapters/discovery.py | GitHub/RSS discovery plus strategy-aware provider composition. | Research external providers / P6 | R1;R5 | PROD_CONDITIONAL | src/research_domain/models.py;src/research_domain/providers.py | src/research_adapters/__init__.py |  | MEDIUM |
| src/research_adapters/web.py | SearXNG discovery and static-first web content adapters. | Research external providers / P6 | R1;R5 | PROD_CONDITIONAL | src/research_domain/models.py;src/research_domain/providers.py | src/research_adapters/__init__.py |  | MEDIUM |
| src/orchestration/research_schedule.py | 将 PostgreSQL Task schedule 版本投影到 Temporal Schedule | Research schedule reconciliation / P6 | R1 | PROD_CONDITIONAL | src/orchestration/research_workflow.py;src/orchestration/web_workflow.py;src/persistence/uow.py | src/orchestration/worker.py |  | MEDIUM |

## Artifact

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/web_artifacts/services.py | 定义 ArtifactService;_digest | Artifact versions / S1 | R2 | PROD_CONDITIONAL | src/persistence/repositories.py;src/persistence/uow.py;src/web_domain/errors.py;src/web_domain/services.py | src/web_api/app.py;src/web_artifacts/__init__.py |  | MEDIUM |
| src/web_artifacts/build.py | 定义 ArtifactBuildService | Artifact versions / S1 | R1;R2;R3;R5 | PROD_CONDITIONAL | src/common/logging.py;src/persistence/uow.py;src/web_artifacts/generator.py | src/orchestration/artifact_activities.py;src/orchestration/worker.py;src/web_api/fake_executor.py |  | MEDIUM |
| src/web_artifacts/generator.py | 定义 ArtifactGenerationError;ModelResource;WebArtifactGenerator | Artifact versions / S1 | R1;R2;R3;R5 | PROD_CONDITIONAL |  | src/orchestration/worker.py;src/web_api/fake_executor.py;src/web_artifacts/build.py |  | MEDIUM |
| src/orchestration/artifact_dispatcher.py | 定义 ArtifactOutboxDispatcher;TemporalArtifactClient;artifact_workflow_id;run_artifact_dispatcher_loop;run_artifact_outbox_recovery_loop | Process lifecycle & Temporal / MULTIPLE | R1;R3;R5 | PROD_CONDITIONAL | src/common/logging.py;src/orchestration/artifact_workflow.py;src/web_artifacts/models.py;src/web_artifacts/outbox.py | src/orchestration/web_worker.py;src/orchestration/worker.py |  | MEDIUM |
| src/orchestration/artifact_workflow.py | 定义 ArtifactBuildWorkflow | Process lifecycle & Temporal / MULTIPLE | R1;R3;R5 | PROD_CONDITIONAL | src/orchestration/web_workflow.py;src/web_artifacts/models.py | src/orchestration/artifact_dispatcher.py;src/orchestration/web_workers.py |  | MEDIUM |

## Memory / identity

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/account/postgres_account_service.py | PostgresAccountService —— 基于 PostgreSQL identity_bindings 的账号解析。 | Identity & credentials / P2 | R1;R5 | PROD_CONDITIONAL | src/account/identity.py | src/account/__init__.py;src/application/ingress.py;src/bootstrap/qq.py | DUAL_IMPLEMENTATION | MEDIUM |
| src/account/identity_binding_service.py | QQ ownership challenges and narrowly-scoped identity consolidation. | Identity & credentials / P2 | R1;R2 | PROD_CONDITIONAL | src/account/identity.py;src/persistence/uow.py | src/application/identity_commands.py;src/orchestration/worker.py;src/web_api/app.py |  | MEDIUM |
| src/application/context_assembly.py | Read-only Web Context assembly with strict Conversation boundaries. | Application use cases / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/common/logging.py;src/common/types.py;src/harness/context_builder.py;src/memory/hindsight_client.py | src/agent_execution/web_adapters.py;src/orchestration/worker.py |  | MEDIUM |
| src/application/memory_retention.py | MemoryRetentionService —— Web completed Run → 单个 Hindsight document。 | Application use cases / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/common/logging.py;src/persistence/uow.py | src/orchestration/worker.py |  | MEDIUM |
| src/memory/hindsight_client.py | HindsightClient —— Hindsight 记忆服务 REST API 封装（v0.6.1）。 | Long-term / group memory adapters / P2 | R1;R5 | PROD_CONDITIONAL |  | src/application/context_assembly.py;src/memory/__init__.py;src/orchestration/worker.py | LARGE_HOTSPOT | MEDIUM |

## Persistence / storage / workspace

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/persistence/repositories.py | Ownership-scoped repositories.  No method accepts a bare public resource ID. | PostgreSQL runtime / MULTIPLE | R1;R2;R3;R4;R5 | PROD_CONDITIONAL | src/persistence/uow.py | src/application/context_assembly.py;src/file_domain/approvals.py;src/research_domain/services.py |  | MEDIUM |
| src/persistence/uow.py | 定义 UnitOfWork;retryable_transaction | PostgreSQL runtime / MULTIPLE | R1;R2;R3;R4;R5;R6 | PROD_CONDITIONAL |  | src/account/credentials.py;src/account/identity_binding_service.py;src/account/registration_service.py | HIGH_FAN_IN | MEDIUM |
| src/session/store.py | QQ Session 生命周期、Redis热状态、WAL、checkpoint及归档读取接口，供 TurnMemoryService 使用 | QQ short-term persistence / P2 | R1;R5 | PROD_CONDITIONAL | src/common/types.py;src/session/models.py | src/application/memory.py;src/bootstrap/qq.py;src/session/__init__.py | ARCHITECTURE_DRIFT;LARGE_HOTSPOT | MEDIUM |
| src/session/db.py | SQLite WorkspaceDB，保存本地 users/sessions 元数据，区别于 PostgreSQL 身份与 Web session | Workspace metadata persistence / P3 | R1;R5 | PROD_CONDITIONAL | src/session/models.py | src/orchestration/worker.py;src/session/__init__.py;src/session/workspace.py |  | MEDIUM |
| src/session/workspace.py | 初始化账户和 Session 文件布局并写入 WorkspaceDB 元数据 | Workspace provisioning / P3 | R1 | PROD_CONDITIONAL | src/session/db.py;src/session/models.py | src/application/conversation.py;src/application/session_archive.py;src/session/__init__.py |  | MEDIUM |
| src/storage/file_store.py | 通用 LocalFileStore，供 workspace、备份和会话归档使用 | General workspace/archive file adapter / MULTIPLE | R1;R5 | PROD_CONDITIONAL | src/storage/protocols.py | src/bootstrap/qq.py;src/orchestration/worker.py;src/storage/__init__.py |  | MEDIUM |
| src/storage/tenant_file_store.py | 按租户/文件标识保存不可变文件对象，提供只读 reader 与写 store | Tenant file object storage / P5 | R1;R2;R4;R5;R6 | PROD_CONDITIONAL |  | src/file_domain/persistent.py;src/file_runtime/output.py;src/orchestration/document_worker.py | HIGH_FAN_IN | MEDIUM |
| src/workspace/isolation.py | 校验拓扑、持有进程/账户锁、Git恢复检查并准备 Web Session/Run资源 | Shared workspace locking and recovery / P4 | R1;R4;R5 | PROD_CONDITIONAL | src/persistence/repositories.py;src/persistence/uow.py;src/workspace/file_scope.py | src/agent_execution/web_host.py;src/orchestration/web_workers.py;src/orchestration/worker.py |  | MEDIUM |

## Tool / model infrastructure

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/resources/resource_pool.py | ResourcePool —— 模型调用池，实现 IResources 接口。 | Model / retrieval infrastructure / P3 | R1;R4;R5 | PROD_CONDITIONAL | src/agent_execution/model_budget_context.py;src/common/errors.py;src/common/interfaces.py;src/common/model_usage.py | src/agent/factory.py;src/orchestration/worker.py;src/resources/__init__.py |  | MEDIUM |
| src/resources/model_client.py | ModelClient —— 单个模型 API 的 HTTP 客户端。 | Model / retrieval infrastructure / P3 | R1;R5 | PROD_CONDITIONAL | src/common/errors.py;src/common/model_usage.py;src/common/types.py | src/resources/__init__.py;src/resources/resource_pool.py | LARGE_HOTSPOT | MEDIUM |
| src/sandbox/sandbox_manager.py | SandboxManager —— 沙箱池管理器，按会话创建 workspace 绑定的沙箱。 | Tool runtime / workspace execution / P3 | R1;R4;R5 | PROD_CONDITIONAL | src/common/errors.py;src/sandbox/nsjail.py;src/sandbox/sandbox.py;src/sandbox/tools/local/__init__.py | src/orchestration/worker.py;src/sandbox/__init__.py |  | MEDIUM |
| src/sandbox/tools/registry.py | Canonical tool catalog and executor. | Tool runtime / workspace execution / P3 | R1;R4;R5 | PROD_CONDITIONAL | src/sandbox/tools/routing/models.py;src/sandbox/tools/types.py | src/sandbox/sandbox.py;src/sandbox/sandbox_manager.py;src/sandbox/tools/__init__.py |  | MEDIUM |
| src/sandbox/tools/routing/router.py | 定义 ToolRouter | Tool runtime / workspace execution / P3 | R1;R4;R5 | PROD_CONDITIONAL | src/sandbox/tools/routing/capability.py;src/sandbox/tools/routing/models.py;src/sandbox/tools/routing/policy.py;src/sandbox/tools/routing/projector.py | src/sandbox/sandbox.py;src/sandbox/tools/routing/__init__.py |  | MEDIUM |
| src/sandbox/tools/adapters/mcp.py | MCP 多传输客户端 —— 支持 HTTP / SSE / stdio 三种传输方式连接 MCP Server。 | Tool runtime / workspace execution / P3 | R1;R5 | PROD_CONDITIONAL | src/sandbox/tools/types.py | src/orchestration/worker.py;src/sandbox/tools/adapters/__init__.py | LARGE_HOTSPOT | MEDIUM |
| src/sandbox/tools/skills/engine.py | SkillPipeline —— Skills 复合工具编排引擎。 | Tool runtime / workspace execution / P3 | R1;R4;R5 | PROD_CONDITIONAL | src/sandbox/tools/types.py | src/sandbox/sandbox_manager.py;src/sandbox/tools/skills/__init__.py |  | MEDIUM |

## Frontend

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| web/src/main.tsx | 创建 React root 并挂载主题和 App | Web frontend / P1 | R7 | PROD_DIRECT |  |  |  | HIGH |
| web/src/App.tsx | 认证门禁、主工作台布局、Conversation选择和 Artifact/Trace 面板协调 | Web frontend / MULTIPLE | R7 | PROD_CONDITIONAL |  |  |  | MEDIUM |
| web/src/components/ChatPane.tsx | 聊天composer、策略选择、附件、审批与Run状态整合 | Web frontend / MULTIPLE | R7 | PROD_CONDITIONAL |  |  |  | MEDIUM |
| web/src/store/workbench.ts | Conversation/Message/Run、发送/重试/取消、附件及SSE降级轮询状态 | Web frontend / MULTIPLE | R7 | PROD_CONDITIONAL |  |  | LARGE_HOTSPOT | MEDIUM |
| web/src/api/resources.ts | 封装Conversation/Message/Run、文件、审批、Artifact和身份API | Web frontend / MULTIPLE | R7 | PROD_CONDITIONAL |  |  |  | MEDIUM |
| web/src/sse/runFeed.ts | Run事件去重/排序、终态快照处理、Trace事件及降级回调 | Web frontend / S2 | R7 | PROD_CONDITIONAL |  |  |  | MEDIUM |
| web/src/components/trace/TracePanel.tsx | 按Run打开Trace、刷新和树/详情布局 | Web frontend / S2 | R7 | PROD_CONDITIONAL |  |  |  | MEDIUM |
| web/src/components/ArtifactPanel.tsx | Artifact与版本面板、生成新版本和预览协调 | Web frontend / S1 | R7 | PROD_CONDITIONAL |  |  |  | MEDIUM |

## Ops / scripts / migrations

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/persistence/migrate.py | 按序读取根 persistence/migrations SQL，通过迁移连接执行并记录版本 | Database migration operations / MULTIPLE | R8 | OPS_ADMIN |  |  | OPS_ONLY_USAGE | MEDIUM |
| src/account/bootstrap.py | 显式建立 PostgreSQL Web/QQ 身份绑定，供 bootstrap 脚本调用 | Identity administration / P2 | 未闭合 | OPS_ADMIN | src/account/identity.py |  | OPS_ONLY_USAGE | MEDIUM |
| scripts/merge-account.py | 合并渠道账号 —— 将不同渠道的身份绑定到同一个 account_id。 | Repository / operations / NONE | 未闭合 | OPS_ADMIN |  |  |  | MEDIUM |
| scripts/backup.sh | 文件资产：backup_restore | Repository / operations / NONE | 未闭合 | OPS_ADMIN |  |  |  | MEDIUM |
| scripts/capture_web_workflow_history.py | Explicitly capture a real ``WebRunWorkflow`` History as a frozen fixture. | Repository / operations / NONE | 未闭合 | OPS_ADMIN | src/orchestration/web_dispatcher.py;src/orchestration/web_workers.py;src/orchestration/web_workflow.py |  |  | MEDIUM |

## Experimental / dev

| 文件 | 职责 | Owner / capability | Roots | 可达性 | 主要静态依赖 | 主要静态使用者 | Flags | 置信度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| src/agent/adapters.py | Legacy compatibility adapter for objects exposing ``process_turn``. | Mixed protocol / experimental Multi-Agent / NONE | 未闭合 | EXPERIMENTAL_REJECTED | src/agent/context.py;src/agent/interfaces.py;src/agent/types.py | src/agent/__init__.py | EXPERIMENTAL_CODE;PRODUCTION_REJECTED | MEDIUM |
| src/agent/factory.py | 编排器工厂 —— 一行构建配置好的多Agent编排器。 | Mixed protocol / experimental Multi-Agent / NONE | 未闭合 | EXPERIMENTAL_REJECTED | src/agent/bus.py;src/agent/interfaces.py;src/agent/orchestrator.py;src/agent/registry.py | src/agent/__init__.py;src/agent/runner.py | EXPERIMENTAL_CODE;PRODUCTION_REJECTED | MEDIUM |
| src/agent/runner.py | Experimental MultiAgent executor. | Mixed protocol / experimental Multi-Agent / NONE | 未闭合 | EXPERIMENTAL_REJECTED | src/agent/context.py;src/agent/factory.py;src/agent/llm_agent.py;src/agent/orchestrator.py | src/agent/__init__.py | EXPERIMENTAL_CODE;PRODUCTION_REJECTED | MEDIUM |
| src/web_api/fake_executor.py | 开发配置下模拟 Run 和 Artifact 完成/失败，production settings 禁止启用 | Web development execution simulator / NONE | R2 | DEV_ONLY | src/agent_execution/web_events.py;src/common/types.py;src/persistence/uow.py;src/web_api/config.py | src/web_api/app.py | DEV_ONLY_USAGE | MEDIUM |
| src/channels/console.py | ConsoleChannel —— 控制台协议渠道，通过标准输入输出与用户交互。 | QQ transport / P1 | 未闭合 | UNKNOWN | src/channels/base.py;src/common/types.py | src/channels/__init__.py | ARCHITECTURE_DRIFT;UNRESOLVED_DYNAMIC_EDGE | LOW |

## UNKNOWN / unresolved

完整列表见 unresolved_items.csv。包初始化/重导出行保留 UNKNOWN，表示未把导入执行等同业务使用；协议和纯 DTO 不一定有独立调用入口。符号下钻行精确到方法，未闭合的方法归入按文件分组的 SYMBOL_COVERAGE 问题，没有继承整个文件的生产标签。对于 AccountService、ConsoleChannel、Skill installer 这类需要外部/人为入口证据的文件，没有出具 UNREACHABLE_PROVEN。

## Ownership 与数据的读取方法

structural_parent 是目录事实；logical_owner 是本轮根据定义、组合和 SQL 角色给出的审计解释，不是仓库维护者/CODEOWNERS。instantiated_by 记录构造位置，called_by 记录调用位置，不与 imported_by 混用。file 级 reads/writes 来自该文件 SQL literal，symbol 级来自具体函数；跨进程/文件系统/Redis/Hindsight 资源见 storage_ownership.csv。空字段表示未提取到直接事实，不代表没有传递副作用。

## 当前热点

| 文件 | LOC | fan-in | fan-out |
| --- | --- | --- | --- |
| src/sandbox/tools/adapters/mcp.py | 1545 | 4 | 15 |
| src/agent_activities/runtime.py | 1499 | 6 | 23 |
| src/orchestration/worker.py | 1370 | 5 | 84 |
| src/web_api/app.py | 1148 | 6 | 45 |
| src/agent/strategies.py | 944 | 9 | 9 |
| src/channels/official_qq.py | 905 | 2 | 10 |
| web/src/store/workbench.test.ts | 850 | 0 | 5 |
| src/orchestration/config.py | 810 | 8 | 8 |
| src/research_activities/runtime.py | 807 | 1 | 22 |
| web/src/store/workbench.ts | 794 | 5 | 8 |
| src/session/store.py | 736 | 3 | 9 |
| src/agent_execution/brain_action_loop.py | 705 | 5 | 12 |
| src/resources/model_client.py | 673 | 4 | 9 |
| src/memory/hindsight_client.py | 662 | 7 | 7 |
| src/web_domain/services.py | 627 | 27 | 18 |
| src/agent_execution/qq_host.py | 593 | 5 | 11 |
| src/persistence/repositories.py | 550 | 10 | 6 |
| src/channels/napcat.py | 543 | 2 | 9 |
