# 按修改任务找代码

先读[总览](../architecture/overview.md)。下面沿真实调用边界定位，目录是职责入口，并不表示已实现严格依赖隔离。

## 进程与组合入口

| 入口 | 职责 |
| --- | --- |
| [main.py](../../src/main.py)、[bootstrap](../../src/bootstrap/) | QQ 入口与共享运行时组装。 |
| [web_api/__main__.py](../../src/web_api/__main__.py)、[app.py](../../src/web_api/app.py) | HTTP 进程与应用工厂；[lifespan.py](../../src/web_api/lifespan.py) 持有连接池、Redis、发布器及测试执行器生命周期。 |
| [worker.py](../../src/orchestration/worker.py) | 进程启动/关闭和渠道监听；[worker_dependencies.py](../../src/bootstrap/worker_dependencies.py) 构造共享资源，[durable_runtime.py](../../src/bootstrap/durable_runtime.py) 注入 Activity 与持久执行协作者，[web_workers.py](../../src/orchestration/web_workers.py) 注册队列。 |
| [document_worker.py](../../src/orchestration/document_worker.py) | 高开销文档 Activity 独立进程。 |
| [docker-compose.yaml](../../docker-compose.yaml)、[Makefile](../../Makefile) | 服务拓扑、公开开发/验证命令。 |

## 服务端改动路径

| 要修改什么 | 规则/命令 → 执行/存储 → 验证 |
| --- | --- |
| 聊天接收、幂等、单 active Run | [application/chat](../../src/application/chat/) 的 SendMessage、RetryChatRun 和 projection；[application/run](../../src/application/run/) 的共享 CancelRun → 原 Repository / [run_domain](../../src/run_domain/)；[conversation_domain](../../src/conversation_domain/) 保留准入策略及兼容 facade → [web_persistence 测试](../../test/web_persistence/)。 |
| Work 接受、查询、显式推进与准入 | [application/work](../../src/application/work/) 的 AcceptWork、WorkQueries、AdvanceWork、AdmitWorkRun；[work_domain/admission.py](../../src/work_domain/admission.py) 是纯门禁决定，[run_domain/creation.py](../../src/run_domain/creation.py) 构造纯snapshot/预算值；[application/work/service.py](../../src/application/work/service.py) 组合 Accept/Advance 并保留 revise/control/link/integration；旧 work_domain/commands.py 同名导出 → 现有持久化接口 → [test/work_domain](../../test/work_domain/)。 |
| Work 修订、暂停、停止、完成 | [application/work/service.py](../../src/application/work/service.py)、[execution_result.py](../../src/work_domain/execution_result.py)（Generic 最终候选解析与事务内证据核验）、[completion.py](../../src/work_domain/completion.py)、[progress.py](../../src/work_domain/progress.py)（revision 保护与 daily 基线）→ 指定行仓储；[application/work/events.py](../../src/application/work/events.py) 借用同UoW协调事件、通知和Outbox。[persistence.py](../../src/work_domain/persistence.py) 仅兼容导出/转发。 |
| PG Work 调度与恢复 | [evaluate_schedule.py](../../src/application/work/evaluate_schedule.py) 提交 schedule/occurrence 评估，再调用 [dispatch_due_work.py](../../src/application/work/dispatch_due_work.py) 的独立批次事务及逐Work savepoint；[application/work/scheduler.py](../../src/application/work/scheduler.py) 组合调度用例；orchestration/work_schedule.py 保留循环及服务兼容导出。 |
| admission、冻结输入、Run 终态/重试 | [run_domain](../../src/run_domain/) 的 strategy/creation/lifecycle；creation 冻结 ChatRunInsert/WorkRunInsert，由 [persistence/runs.py](../../src/persistence/runs.py) 分别 insert_chat/insert_work；Work准入实现位于 application/work/admit_work_run.py，run_domain/admission.py保留兼容函数 → [agent_lifecycle_workflow.py](../../src/orchestration/agent_lifecycle_workflow.py)；[execution_strategy.py](../../src/orchestration/execution_strategy.py) 只兼容导出纯策略。 |
| SQL writer 与技术事实 | [persistence](../../src/persistence/) 的 accounts/conversations/messages/runs、works/work_requirements/wakeups/work_schedules/work_events；[outbox.py](../../src/persistence/outbox.py) 持有队列SQL，[workflow_executions.py](../../src/persistence/workflow_executions.py) 持有Workflow事实SQL。旧聚合repositories及Work persistence保留兼容导出，外围File/Budget/Idempotency未机械拆分。 |
| 执行身份、fencing、回执、分支事实 | [execution.py](../../src/run_domain/execution.py)、[execution_context.py](../../src/run_domain/execution_context.py)、[results.py](../../src/run_domain/results.py)、[delegation.py](../../src/run_domain/delegation.py)；[capability_contracts.py](../../src/run_domain/capability_contracts.py) 固定能力/结果/检查/采用值对象，[capabilities.py](../../src/run_domain/capabilities.py) 保存调用、读取、采用与观察关联；[acceptance.py](../../src/run_domain/acceptance.py) 提供父侧采用/保存与 typed evidence；函数复用调用方事务，不依赖 Activity。 |
| Agent 策略、attempt、工具、Subagent | [agent_workflows](../../src/agent_workflows/) → [agent_activities](../../src/agent_activities/)（[通用能力调用](../../src/agent_activities/capabilities.py)与[稳定子 Workflow](../../src/agent_workflows/capability.py)）、[application](../../src/application/)（[work_context.py](../../src/application/work_context.py) 负责 Work/Subagent 输入加载、来源路由与执行身份绑定）→ [sandbox 契约测试](../../test/test_durable_agent_contract.py)。 |
| 记忆、治理与上下文撤回 | [context_assembly.py](../../src/application/context_assembly.py)、[memory_retention.py](../../src/application/memory_retention.py)、[memory_governance.py](../../src/application/memory_governance.py) → [MemoryStore](../../src/persistence/memory.py)、[上下文失效](../../src/persistence/memory_context.py)、[Hindsight Adapter](../../src/memory/hindsight_client.py)；[scope_policy](../../src/memory/scope_policy.py) 统一范围，[治理 API](../../src/web_api/routes/memory.py) 提交命令，沿现有 Outbox/Memory Worker 推进。契约见[能力边界](../architecture/capabilities.md#memory)。 |
| 模型、额度、并发、Provider 协议 | [resources](../../src/resources/)、[config](../../config/) → [模型分工](model-routing.md)、[能力边界](../architecture/capabilities.md)。 |
| 文件、授权、版本、发布/保存、GC | [workspace](../../src/workspace/)、[file_lifecycle.py](../../src/web_domain/file_lifecycle.py)、[file_runtime](../../src/file_runtime/) → [Workspace 契约](../architecture/workspace.md)。 |
| 研究与成果生成 | [research_domain](../../src/research_domain/)、[research_activities](../../src/research_activities/) （局部运行时与 [父适配器](../../src/research_activities/parent.py)）→ [research_workflow.py](../../src/orchestration/research_workflow.py)、[artifact_activities.py](../../src/orchestration/artifact_activities.py)；[HTML 局部实现](../../src/web_artifacts/local.py) 通过 [HtmlCapabilityWorkflow](../../src/agent_workflows/html.py) 调用共享 generator/publisher；[文字发布](../../src/agent_activities/text_result.py) 经 [TextCapabilityWorkflow](../../src/agent_workflows/text.py) 复用不可变文件与 ResultRef 内核。 |
| 共享 Web Search/Fetch | [web_retrieval](../../src/web_retrieval/) 提供供应商选择、安全抓取与预算边界；[web_tools.py](../../src/agent_activities/web_tools.py) 向 Main / Work root 注入工具，Research 沿共享 Adapter 使用同一服务。 |
| QQ 反向 WS、鉴权与协议 | [onebot11.py](../../src/channels/onebot11.py)、[onebot.py](../../src/common/onebot.py) → [ingress.py](../../src/application/ingress.py)、[conversation.py](../../src/application/conversation.py) → [surfaces/conversation.py](../../src/application/surfaces/conversation.py) 的身份/路由/receipt事务 → Chat/Run公开事务内用例；[qq_diagnostics.py](../../src/orchestration/qq_diagnostics.py) 单独报告 QQ readiness。 |
| 显式 HTML 创建、版本与查询 | [application/artifacts/commands.py](../../src/application/artifacts/commands.py) 持有 Artifact/Version 与 HTML Run 的写事务；[application/artifacts/service.py](../../src/application/artifacts/service.py) 组合命令和查询；web_artifacts/services.py 保留同名兼容导出，[queries.py](../../src/web_artifacts/queries.py) 负责原查询/投影，生成执行仍在原包。API组合根显式注入 legacy 预算兼容值。 |
| 通知/逐片投递/QQ 回执 | [delivery](../../src/delivery/)、[qq_delivery.py](../../src/application/qq_delivery.py) → [router.py](../../src/channels/router.py)、[channel_result.py](../../src/common/channel_result.py)；provider accepted/failed/uncertain 不代替 Run 或 Work 终态。 |
| API、权限、快照、SSE、诊断 | [routes](../../src/web_api/routes/)、[dependencies.py](../../src/web_api/dependencies.py)、[middleware.py](../../src/web_api/middleware.py)、[error_handlers.py](../../src/web_api/error_handlers.py) → [web_domain](../../src/web_domain/)、[persistence](../../src/persistence/) → [API 测试](../../test/web_api/)。 |
| schema/角色权限 | [migrations](../../persistence/migrations/) → [数据库测试](../development/testing.md)；新增有序迁移，不修改已应用文件。 |

定位具体测试先使用 `rg --files test` 与相关领域名搜索，不按文件名中的旧阶段编号推断当前覆盖范围。

## 前端改动路径

| 要修改什么 | 入口 |
| --- | --- |
| 认证、账户切换 | [App.tsx](../../web/src/App.tsx)、[store](../../web/src/store/)、[api](../../web/src/api/)。 |
| 导航、唯一 Inspector、Surface | [shell.ts](../../web/src/store/shell.ts)、[components/shell](../../web/src/components/shell/)。 |
| 对话、Composer、阅读与切换 | [conversation](../../web/src/components/conversation/)、[assistant-ui adapter](../../web/src/adapters/assistant-ui/)、[mercury](../../web/src/components/mercury/)。 |
| 任务、文件、成果、执行诊断 | [tasks](../../web/src/components/tasks/)、[workspace](../../web/src/components/workspace/)、[artifact](../../web/src/components/artifact/)、[run](../../web/src/components/run/)、[trace](../../web/src/components/trace/)。 |
| 对话状态与生命周期 | [workbench.ts](../../web/src/store/workbench.ts) 门面与 reset；[workbench](../../web/src/store/workbench/) 内部动作、共享 context 和 Run monitor。 |
| 权威快照、幂等、分页、订阅 | [store](../../web/src/store/)、[sse](../../web/src/sse/)、[api](../../web/src/api/)。 |
| 视觉与动效 | [styles.css](../../web/src/styles.css) 的显式导入顺序、[styles](../../web/src/styles/) 语义 tokens；行为见 [Web 契约](../architecture/web-ui.md)。 |
| 验证 | 组件/store 相邻单测、[e2e](../../web/e2e/)、[共享浏览器配置](../../web/e2e/config/shared.ts)、[package.json](../../web/package.json) 与 [测试指南](../development/testing.md)。 |

## 配置与辅助代码

[config](../../config/) 是运行时配置；[scripts](../../scripts/README.md) 按开发、检查、运维与实验划分；[prototypes](../../prototypes/) 是独立实验室，不属于产品构建。迁移与 [test/fixtures](../../test/fixtures/) 是可执行输入，不能作为历史材料删除。截图、日志、trace、benchmark 结果不纳入当前文档；运行输出位置及副作用见脚本索引。

QQ 服务使用 [SnowLuma 启动校验](../../config/snowluma/verify.cjs)、
[初始化脚本](../../scripts/operations/snowluma-config.py) 和 [OneBot11 隔离测试拓扑](../../test/fixtures/onebot11.compose.yaml)。
镜像版本与运行中账号/协议版本分别核验；部署入口见 [SnowLuma / OneBot11](../operations/deployment.md#snowluma--onebot11)。

## 当前业务主干目录与旧路径映射

业务接收与持久化主干如下；能力、Workspace、查询及生命周期仍存在跨领域依赖。

```text
src/application/
  chat/       commands, service, dependencies, send_message, retry_run, projection
  run/        commands, cancel_run
  surfaces/   conversation
  artifacts/  commands, service
  work/       commands, service, dependencies, queries, accept_work, advance_work,
              admit_work_run, scheduler, evaluate_schedule, dispatch_due_work,
              events, schedules
src/run_domain/       creation, strategy, lifecycle, execution, ...
src/work_domain/      admission, scheduling, serialization, errors, completion, ...
src/persistence/      accounts, conversations, messages, runs, works,
                      work_requirements, wakeups, work_schedules, work_events,
                      outbox, workflow_executions, repositories, uow, ...
```

下表中的目标均为当前实现，不是待建目录；未列出的文件原位保留。

| 原位置 | 当前实现及职责 | 旧路径处理 |
| --- | --- | --- |
| conversation_domain/commands.py 的发送、重试、取消、projection | application/chat/{commands,send_message,retry_run,projection,dependencies}.py；application/run/{commands,cancel_run}.py；application/chat/service.py 组合公开 API 并保留原辅助命令 | 只同名导出 CommandService、message_request_id；旧私有事务内方法仍由新 facade 兼容转发 |
| conversation_domain/surface_commands.py | application/surfaces/conversation.py 持有身份、绑定、receipt 及 savepoint | 同名兼容导出 |
| web_artifacts/services.py | application/artifacts/commands.py 持有写事务；service.py 组合命令和原 web_artifacts/queries.py | 同名导出 ArtifactService；生成和发布原位 |
| work_domain/commands.py | application/work/{service,accept_work,advance_work,dependencies,queries}.py；service 保持 revise/control/link/integration 的原事务 | 同名导出 WorkCommandService、WorkConflict |
| run_domain/admission.py | application/work/admit_work_run.py 锁内准入；work_domain/admission.py 纯决定；run_domain/creation.py 冻结值 | 旧 admission 函数兼容转发 |
| orchestration/execution_strategy.py | run_domain/strategy.py 的策略值、Registry、Planner | 同名兼容导出 |
| orchestration/work_schedule.py | application/work/{scheduler,evaluate_schedule,dispatch_due_work}.py；纯计划在 work_domain/scheduling.py | 服务同名导出，进程循环原位 |
| persistence/repositories.py 的 Account/Conversation/Message/Run/Outbox/WorkflowExecution | 对应 accounts/conversations/messages/runs/outbox/workflow_executions.py | 聚合文件保留同名导出；File/RunBudget/Idempotency 仍实际定义于聚合文件 |
| work_domain/persistence.py | persistence/{works,work_requirements,wakeups,work_schedules,work_events}.py；应用 events/schedules 协调；work_domain/serialization.py 只做 DTO | 旧 WorkRepository 继承行仓储并转发旧方法；dto/sync_schedule 兼容 |
| run_dispatcher.py / run_reconciler.py 的纯协议与值 | orchestration/run_lifecycle_contracts.py | 旧路径继续导出；可执行实现和注册原位 |

Web lifespan、Worker 与 worker_dependencies/worker_tasks 直接引用 application facade；QQ 使用公开事务内用例。Workspace 撤权通过 CancelRun.execute_in_uow 借用原 UoW。历史路径仍被原有测试及潜在外部扩展使用，未证明可安全删除，因此保留。WorkCommandDependencies.repo 特意保持旧 WorkRepository 的公开方法集合；主干需求与 wakeup 调用已直接使用窄仓储。这是兼容例外，不是第二套 SQL。

## 核心仓储职责与表写入责任

核心仓储均接收现有 UoW，不拥有连接池、commit、RPC 或通知选择。以下覆盖业务接收主干及直接联动表；能力、记忆和 Workspace 的其他表按各自文档追踪。

| Repository / 文件 | 写入表及职责 | 业务协调者与例外 |
| --- | --- | --- |
| AccountRepository / accounts.py | accounts：当前账号有效性/归属检查 | Chat/Work 入口校验；账号创建、认证、entitlement 仍由原认证模块管理 |
| ConversationRepository / conversations.py | conversations：创建、锁、序号 | Chat 命令；QueryService.rename_conversation 的标题更新仍原位 |
| MessageRepository / messages.py | messages：用户/助手消息及状态 | SendMessage、RetryChatRun、Run lifecycle；Work Run 无 Message |
| RunRepository / runs.py | runs：分别 insert_chat/insert_work、原条件状态更新 | Chat/Work 应用准入、取消和 Run lifecycle；ResourcePolicy 的记忆撤回拒绝标记仍原位 |
| WorkRepository / works.py | works：接受、revision、协调权、continuation、控制与完成状态 | Accept/Advance/Admit、service revise/control、completion/progress/integration；锁和 revision/epoch 由调用方先核验 |
| WorkRequirementRepository / work_requirements.py | work_requirements：不可变 revision 插入/读取 | Accept、revise、准入/查询；不修改旧需求 |
| WakeupRepository / wakeups.py | work_wakeups：入队、选择、消费、取消 | 接受、控制、推进、调度及终态后续唤醒 |
| WorkScheduleRepository / work_schedules.py | work_schedules、work_schedule_occurrences：版本、到期、occurrence | sync_schedule/EvaluateSchedule；计划决定在 domain，时钟按原分支读取 |
| WorkEventRepository / work_events.py | work_events：事件序号及受限 payload | record_work_event 接着调用通知和 Outbox，同 UoW |
| OutboxRepository / outbox.py | outbox_events：入队、claim、recover、ack/retry/dead | 业务命令同事务入队；OutboxService 自己持有消费短事务，不调用外部 RPC |
| WorkflowExecutionRepository / workflow_executions.py | workflow_executions：启动准备、远端观察事实及重试标记 | PostgresWorkflowExecutionStore 短事务；不是 Run 业务完成判据 |
| FileRepository / repositories.py | stored_files、message_files、run_files：上传状态/附件引用 | 文件服务、发送/重试；资源授权仍由 ResourcePolicy，字节仍由 File Store 管理 |
| RunBudgetRepository / repositories.py | run_budgets：创建原预算快照 | Chat/Work 准入；后续预留、结算、Work/Account 账本由 resources 中的预算服务管理 |
| IdempotencyRepository / repositories.py | idempotency_commands：占位、payload 校验、结果缓存 | 所有命令；原幂等键与响应值不变 |

关联表有独立 writer，不能用“RunRepository 写全部运行表”概括：

| 核心关联表 | 当前写入责任 |
| --- | --- |
| run_executions 及 Execution attempt/lease/transcript/operation/receipt 事实 | Run 创建时数据库 trigger 生成 root；run_domain/execution、results、delegation 与原 Agent store/Activity 写执行事实 |
| outbox_events 的 run.* 事件 | ChatCommandDependencies 与 run_domain/lifecycle 通过原 Outbox 路径追加 |
| idempotency_commands、work_conversations | WorkCommandDependencies 的幂等命令/关联；service 保留控制编排 |
| works.checkpoint/completion_receipt、work_progress、work_progress_facts、work_progress_samples、work_daily_results、work_daily_result_refs | 原 Work progress/completion 与 WorkRepository；与当前 revision 校验同事务；验收证据另见能力文档 |
| artifacts、artifact_versions | ArtifactCommands 创建/版本事务；原 Artifact store 与发布/采用服务写生产、发布和采用事实；Run 成功不自动代表保存或验收 |
| conversation_ingress_receipts、conversation_bindings、identity_bindings | SurfaceConversationCommands 的身份路由/receipt 事务与原身份绑定服务 |
| conversation_resource_files、resource_grants、resource_policy_versions、run_resource_candidates、run_resource_snapshots、run_resource_access、run_resource_denials、run_files | ResourcePolicy 管理当前授权与冻结候选；ArtifactCommands 保留显式文件关联；Workspace 及授权事实见 Workspace 文档 |
| notifications、delivery_targets、deliveries、delivery_decisions | delivery/service 及原投递 store；record_work_event 借用同事务入队；真实发送在提交后 |
| work_artifacts、work_input_refs、work_result_acceptances | work_domain/integration 的原关联、输入、用户验收事务；采用精确版本且复验当前控制权 |
| run_budgets、run_usage_ledger、work_budgets、work_usage_ledger | 准入创建预算；resources/run_budget.py、work_budget.py 做后续预留/结算，model_budget_coordinator.py 协调同事务收敛 |
| model_input_snapshots、model_dispatches、account_daily_model_budgets、account_model_usage_ledger | resources/model_input_snapshot.py、account_daily_budget.py 与 model_budget_coordinator.py；快照、发送资格和费用分别记账 |
| capacity_queue、capacity_turns | resources/capacity.py 的跨进程排队/票据与准入协作；不是 Work 协调权或 Execution lease |
| execution_control_events | Work facade 控制协调者时记录取消意图；Execution 控制器据此收敛，不直接推断 Work 完成 |

## 从简单到复杂阅读业务代码

1. 先读 [SendMessageCommand](../../src/application/chat/commands.py) 和 [SendMessage](../../src/application/chat/send_message.py)，沿 execute → execute_in_uow → runs.insert_chat → Outbox 找到同事务写集，再对照 test_conversation_commands 的单槽、重放和回滚断言。
2. 读 [Chat service](../../src/application/chat/service.py) 和 [QQ 外层](../../src/application/surfaces/conversation.py)，比较独立 UoW 与借用 UoW/savepoint；再读 ArtifactCommands 的 Version 与 HTML Run 原子性。
3. 读 [AcceptWork](../../src/application/work/accept_work.py) → AdvanceWork → AdmitWorkRun，区分委托与一次有限执行，跟踪当前 revision、control_epoch、资源与预算的锁内核验。
4. 读 [EvaluateSchedule](../../src/application/work/evaluate_schedule.py) → DispatchDueWork，确认两个事务和逐 Work 失败隔离；再读 Work completion/progress 理解 Run 终态怎样提出下一 continuation。
5. 最后读 [OutboxService](../../src/web_domain/outbox.py) → RunDispatcher → WorkflowExecutionStore → Activity/Run lifecycle，并对照[完整调用图、时序及事务所有权](../architecture/execution.md#业务主干调用图)，理解业务提交、远端执行、版本发布、Work 完成和投递是独立事实。

验证入口见[测试指南](../development/testing.md#业务命令与持久化选测)，运行约束及待核验风险见[执行与恢复](../architecture/execution.md#当前限制与待核验风险)。当前事实以本页和各领域文档为准。
