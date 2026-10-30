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
| 聊天接收、幂等、单 active Run | [conversation_domain](../../src/conversation_domain/) → [run_domain](../../src/run_domain/)、[run_dispatcher](../../src/orchestration/run_dispatcher.py) → [web_persistence 测试](../../test/web_persistence/)。 |
| Work 需求、修订、暂停、停止、完成 | [work_domain/commands.py](../../src/work_domain/commands.py)、[execution_result.py](../../src/work_domain/execution_result.py)（Generic 最终候选解析与事务内证据核验）、[completion.py](../../src/work_domain/completion.py)、[progress.py](../../src/work_domain/progress.py)（revision 保护与 daily 基线）→ [persistence.py](../../src/work_domain/persistence.py)、[work_schedule.py](../../src/orchestration/work_schedule.py) → [test](../../test/) 中相关 Work 测试。 |
| admission、冻结输入、Run 终态/重试 | [run_domain](../../src/run_domain/) → [agent_lifecycle_workflow.py](../../src/orchestration/agent_lifecycle_workflow.py)、[execution_strategy.py](../../src/orchestration/execution_strategy.py)。 |
| 执行身份、fencing、回执、分支事实 | [execution.py](../../src/run_domain/execution.py)、[execution_context.py](../../src/run_domain/execution_context.py)、[results.py](../../src/run_domain/results.py)、[delegation.py](../../src/run_domain/delegation.py)；[capability_contracts.py](../../src/run_domain/capability_contracts.py) 固定能力/结果/检查/采用值对象，[capabilities.py](../../src/run_domain/capabilities.py) 保存调用、读取、采用与观察关联；[acceptance.py](../../src/run_domain/acceptance.py) 提供父侧采用/保存与 typed evidence；函数复用调用方事务，不依赖 Activity。 |
| Agent 策略、attempt、工具、Subagent | [agent_workflows](../../src/agent_workflows/) → [agent_activities](../../src/agent_activities/)（[通用能力调用](../../src/agent_activities/capabilities.py)与[稳定子 Workflow](../../src/agent_workflows/capability.py)）、[application](../../src/application/)（[work_context.py](../../src/application/work_context.py) 负责 Work/Subagent 输入加载、来源路由与执行身份绑定）→ [sandbox 契约测试](../../test/test_durable_agent_contract.py)。 |
| 记忆、治理与上下文撤回 | [context_assembly.py](../../src/application/context_assembly.py)、[memory_retention.py](../../src/application/memory_retention.py)、[memory_governance.py](../../src/application/memory_governance.py) → [MemoryStore](../../src/persistence/memory.py)、[上下文失效](../../src/persistence/memory_context.py)、[Hindsight Adapter](../../src/memory/hindsight_client.py)；[scope_policy](../../src/memory/scope_policy.py) 统一范围，[治理 API](../../src/web_api/routes/memory.py) 提交命令，沿现有 Outbox/Memory Worker 推进。契约见[能力边界](../architecture/capabilities.md#memory)。 |
| 模型、额度、并发、Provider 协议 | [resources](../../src/resources/)、[config](../../config/) → [模型分工](model-routing.md)、[能力边界](../architecture/capabilities.md)。 |
| 文件、授权、版本、发布/保存、GC | [workspace](../../src/workspace/)、[file_lifecycle.py](../../src/web_domain/file_lifecycle.py)、[file_runtime](../../src/file_runtime/) → [Workspace 契约](../architecture/workspace.md)。 |
| 研究与成果生成 | [research_domain](../../src/research_domain/)、[research_activities](../../src/research_activities/) （局部运行时与 [父适配器](../../src/research_activities/parent.py)）→ [research_workflow.py](../../src/orchestration/research_workflow.py)、[artifact_activities.py](../../src/orchestration/artifact_activities.py)；[HTML 局部实现](../../src/web_artifacts/local.py) 通过 [HtmlCapabilityWorkflow](../../src/agent_workflows/html.py) 调用共享 generator/publisher；[文字发布](../../src/agent_activities/text_result.py) 经 [TextCapabilityWorkflow](../../src/agent_workflows/text.py) 复用不可变文件与 ResultRef 内核。 |
| 共享 Web Search/Fetch | [web_retrieval](../../src/web_retrieval/) 提供供应商选择、安全抓取与预算边界；[web_tools.py](../../src/agent_activities/web_tools.py) 向 Main / Work root 注入工具，Research 沿共享 Adapter 使用同一服务。 |
| QQ 反向 WS、鉴权与协议 | [onebot11.py](../../src/channels/onebot11.py)、[onebot.py](../../src/common/onebot.py) → [ingress.py](../../src/application/ingress.py)、[conversation.py](../../src/application/conversation.py) → Conversation 命令；[qq_diagnostics.py](../../src/orchestration/qq_diagnostics.py) 单独报告 QQ readiness。 |
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
