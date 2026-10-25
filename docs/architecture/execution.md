# 执行、状态与恢复

本文维护跨领域的状态权威和执行约束；具体委托与文件规则见 [Work](work.md) 和 [Workspace](workspace.md)，能力实现见[能力边界](capabilities.md)。

## 状态权威

| 存储 | 负责什么 |
| --- | --- |
| PostgreSQL | Account/Identity/entitlement、Conversation/Message、Work/requirement/checkpoint/调度、Run/Execution/transcript/operation/receipt/lease/fence、预算/容量、文件元数据/授权/版本/保留、Research/Artifact、Trace、通知/投递/决议及 Outbox。必须一致的转换同事务提交。 |
| Redis | 短期 Event Context、通知、工具与运行时缓存。丢失不改变已提交业务事实，订阅恢复依赖权威查询。 |
| Hindsight | 长期语义 Memory Bank 与检索索引；应用 PG 保存来源、治理动作、撤权与上下文失效事实，不复制语义索引。 |
| TenantFileStore | 不可变文件字节；所有权、入口、版本、授权和 GC 引用仍在 PG。多个入口可引用同一 file_id。 |
| Git | 明确代码任务的账号级代码资源；普通文件流程和执行身份不依赖 Git。 |

注册事务把邀请码 entitlement profile 复制到 account_entitlements，并记录来源；后续权限以 account_entitlements 为准，修改邀请不追改账号。账号日额度使用 UTC 日账本，与累计 Work 和 Run 预算独立；revision、重试和 Subagent 不重置 Work 账本。

Run succeeded 只说明有限执行成功；Work completed 需要当前 revision 的证据。Message/Artifact 的 completed、发布、保存、版本采用、通知 accepted、已读和用户验收分别处理。Work Run 没有聊天 Message。业务执行不使用 Session 身份；浏览器认证 session/cookie 仍有效。

## HTTP 应用边界

[app.py](../../src/web_api/app.py) 只创建应用并装配协议层、错误映射和业务 Router。[lifespan.py](../../src/web_api/lifespan.py) 用 AsyncExitStack 持有 PG 连接池、可选 Redis、终态发布器和仅测试使用的 Fake 执行器；资源取得后立即登记释放，启动失败同样逆序回收。

[dependencies.py](../../src/web_api/dependencies.py) 统一认证、CSRF、幂等键与文件能力依赖；每次从 request.app 取所属应用的配置与服务，不使用全局应用状态。[middleware.py](../../src/web_api/middleware.py) 执行请求类型、body 大小与公共响应头协议；[error_handlers.py](../../src/web_api/error_handlers.py) 统一映射 HTTP/领域异常，不承担业务事务。

[routes](../../src/web_api/routes/) 按 health、auth、conversations、works、files、runs、artifacts 分组。路由负责参数、权限依赖、服务调用及 HTTP 投影；业务事务继续由命令服务持有，查询、SSE Gateway、文件发布与权限规则不迁入 Router。完整 HTTP 契约见 [API 参考](../reference/api.md)。

## 命令到终态

1. Web/QQ 把身份与输入转为统一命令。聊天在同一事务写 Message、Run、幂等事实和 start_run Outbox；Conversation 单 active 限制仅作用于聊天。
2. Work 命令提交不可变 requirement、checkpoint、continuation、预算与 wakeup。admission 在 Work 锁下校验 revision/control epoch、协调权、预算、资源及容量，冻结输入和策略，创建无聊天 Message 的 Run/root Execution。
3. Dispatcher 使用稳定 hpagent-web-run-{run_id} 启动 AgentLifecycleWorkflow。reminder 直接入队通知；research_report 由父适配器登记 invocation 并等待 ResearchCapabilityWorkflow，再执行父侧保存/验收；artifact_html 构建成果；chat_agent/work_agent 启动 AgentRunWorkflow（ReAct 或 Plan-and-Execute），子 Workflow ID 为 hpagent-agent-run-{run_id}。
4. Activity 提交 operation/receipt、输出和通用 Run 终态 succeeded/failed/cancelled。聊天投影、WorkCompletionPolicy 与通知分别依据持久事实推进。
5. Work 成功后的 ready continuation 在终态事务去重创建下一 wakeup；等待、blocked、暂停、停止和完成不忙循环。投递独立处理已提交通知，不因渠道失败重新执行 Agent。

所有非确定性 I/O，包括间接导入引出的数据库/模型/工具/文件/网络访问，都放 Activity/Adapter。Workflow 只编排、等待和调用 Activity/Child Workflow。Composition Root 拥有共享 Resource Pool、Sandbox、Brain、Action Runtime、Redis、MCP、Hindsight；可变上下文按 Execution 隔离。

Chat Context 使用对话历史、记忆与授权资料；Work Context 使用冻结需求、checkpoint、输入和候选，不加载关联聊天全文。Generic Work root 可一次委派最多三个只读子 Execution，费用仍属同 Run/Work；串行 plan steps 沿用当前 Execution，不等同并行 Subagent。

复杂调用持久事实使用迁移 060：`capability_invocations` 是不可变描述，caller Operation 是调用生命周期权威，capability Execution 是生产者生命周期权威，`capability_results` 保存专业语义 outcome。三者不互相推断终态。`capability_observations` 关联 caller transcript 与生产 Operation；结果已提交而观察缺失时，CapabilityInvocationWorkflow 复用原有子 Execution，补关闭后重新取得父 segment，并在同一事务中完成 caller Operation、配对 tool_result 与观察关联。capability 与只读 subagent 使用不同角色；两类子执行均受长期资源写入保护，Run 取消/终态会收敛活动子执行并使其 fence 失效。

## 进程与调度

[worker.py](../../src/orchestration/worker.py) 持有进程启动/关闭顺序：先构造共享依赖，再连接 Temporal、启动 Worker 与渠道监听；退出先停 ingress/producer，再退出 Worker，最后关闭共享资源。初始化失败也释放已取得资源。

[worker_dependencies.py](../../src/bootstrap/worker_dependencies.py) 构造基础设施与共享/QQ 服务，并将 AsyncExitStack 所有权交给进程；[durable_runtime.py](../../src/bootstrap/durable_runtime.py) 只组装 Activity、Worker、Dispatcher、Reconciler 和可选 Memory Retention，不启动后台任务。[worker_tasks.py](../../src/orchestration/worker_tasks.py) 集中持有后台任务和 readiness；[memory_schedules.py](../../src/orchestration/memory_schedules.py) 移除旧 Reflect Schedule，仅配置指标 Schedule。

执行上下文由 [execution_context.py](../../src/run_domain/execution_context.py) 传播，校验规则在 [execution.py](../../src/run_domain/execution.py)，分支事实在 [delegation.py](../../src/run_domain/delegation.py)，operation 完成与生产回执在 [results.py](../../src/run_domain/results.py)。这些规则不依赖 Temporal/Activity，接收调用方 UoW，不另开事务。Activity 仍负责心跳、取消和 Temporal 错误转换；Run 终态、Work 推进及通知继续由原事务统一提交。

主 Worker 同时组装 Lifecycle/Agent Worker、Dispatcher 和 PostgreSQL Work due loop。due loop 维护 schedule desired/applied version、occurrence 与 wakeup；容量等待后重新校验。Memory 指标使用 Temporal Schedule；旧全 Bank Reflect Schedule 在启动时移除，归纳由 Hindsight 原生 Consolidation 负责。

Document Workflow 在生命周期队列编排，重型规范化 Activity 在独立 document Worker 执行；稳定 operation 保存结果，不创建另一套 Work/文件状态。队列和注册入口见 [Temporal](../reference/temporal.md)。

退出时先停止 ingress/后台 producer，再退出 Worker，最后逆序关闭共享资源。健康探针检查 schema、Temporal、Lifecycle/Agent Worker 与 Dispatcher；API ready 不能替代 Worker 或真实模型链检查。

## 模型请求与费用

请求顺序：entitlement/tier → Provider serializer 校验 transcript 与 tool-call/result 配对 → 冻结 Model Input Snapshot → 单独事务原子预留 Account/day、Work（若有）、Run → composition 发送前检查 → 记录 dispatch 并分发冻结请求体 → 结算/释放。Anthropic 的独立 system 也计费；工具结果以明确 observation 进入下一轮。快照存在不等于已发送，查询时按当前 entitlement 投影可见字段。

发送前检查可读取包含完整工具 schema 和上下文的冻结请求；拒绝时不登记 dispatch、不开始 HTTP，释放业务预留。发送前失败或取消释放预留；已发送但结果未知按估算结算，避免无主 hold。Snapshot、Trace 与账本各有用途，不能互相代替用量事实。

model_request_invalid、model_response_invalid、model_access_denied、model_request_rejected、model_rate_limited 当前不可自动重试；model_connection_failed、model_read_timeout、model_http_error、model_unavailable 当前可重试。HTTP 429 当前不可自动重试。分类贯穿 Activity、Lifecycle、Run DTO 和重试命令；普通模型故障不归为外部写入结果不确定。独立 Artifact 请求默认 read 90 秒、connect 最多 5 秒；常规聊天依 endpoint timeout。

## 并发、幂等与恢复

- 状态与分发意图同事务提交；Dispatcher lease 允许接管。command key、稳定 Workflow ID、operation/effect key 和生产回执约束重复副作用。未知响应恢复原意图，不换键冒充新请求。
- Work 协调权、Execution attempt/segment lease 与 fencing、显式资源锁、跨进程 capacity_queue/capacity_turns 是四项独立约束。新 token 拒绝旧写入，账户并非全执行独占。
- operation attempt 记录原生产者；旧 attempt/revision 的迟到回执可保留事实，但不得追加当前 transcript、写新 Workspace 结果或完成新需求。
- Approval/wait 持久化后等待 Signal，segment 释放租约；恢复重新获 attempt/fence。Worker 缓存淘汰只销毁内存协程，不在 Workflow 循环外提交结束等待命令；重放恢复仍读取原持久 wait。正常完成、到期与取消继续提交结束等待 Activity。Work revise/pause/stop 提交 control epoch 与取消意图，Run 取消和 Work 控制分别收敛。
- sending lease 失效的外部投递转 uncertain，显式 resolution 后再决定接受/重发。未知外部写入保留 pausing/stopping blocker，不能提前报告停止。
- Run 固定候选不提供永久授权；select/read/save 仍核对当前 grant、revision 与 fence。attempt scratch 隔离，旧清理不能删除新目录。
- PostgreSQL 持久 wait、ready wakeup、once catch-up/daily latest 与容量续租不依赖进程内 timer；SSE/Redis 仅通知恢复，不裁定完成。

## Research 阶段 lease 与恢复

主 Agent store 注入 agent_execution_lease_ttl_seconds（默认 900 秒）。ResearchActivities 独立持有默认 30 秒 lease 的 store，按 invocation 的子 Execution 取得各阶段 segment；获得阶段 segment 后，每 5 秒校验并续租 PG 所有权，再发送 Temporal 心跳。等待其他 segment 时仅发送等待心跳，不延长对方 lease。

续租失败会停止当前阶段并清理后台任务；失效 lease 不能被续活，旧线程的持久写入仍由 fencing 拒绝。Worker 崩溃后，新 attempt 等待旧 lease 自然到期，再以新 token 接管；阶段 operation 与不可变输出发布按 invocation 固定，父保存意图仍按顶层 Run 固定；重试复用原标识。publish/save 的 Activity 窗口为 start-to-close 60 秒、schedule-to-close 120 秒，端到端恢复还受 Temporal 重试、排队和依赖可用性影响，30 秒 lease 不是完成时间承诺。

隔离恢复测试使用实际默认 lease：长阶段跨越初始到期时间仍拒绝抢占；发布或保存提交后 SIGKILL 原 Worker，由替代 Worker 恢复，并校验历史基线、保存意图与结果去重。测试不清 lease、不强制接管，也不缩短默认值。

真实 Provider、生产 Worker、QQ 外发与恢复演练使用实际网络/配置分别验证；隔离或协议测试通过不能替代这些结论。诊断入口见[故障排查](../operations/troubleshooting.md)和[测试指南](../development/testing.md)。

复杂能力通过 [CapabilityInvocationWorkflow](../../src/agent_workflows/capability.py) 在同 Run 的独立 Execution 内运行；父登记和父观察是分离的短 segment，中间等待不持父 lease。子结果、父观察和 Run/Work 终态是独立事实；固定身份恢复、调用上限与取消约束见[能力边界](capabilities.md#通用持久调用与-harness)。


长期记忆上下文使用 `run_executions.memory_context` 和 Snapshot 上的无正文 manifest。
失效提交与模型 dispatch 使用账户锁排序，但按实际读取 scope 的交集拒绝。
执行 fence 和 Run 成功提交检查同一规则，迟到模型结果、工具、子执行及 final/fallback
不能复用被撤回上下文；历史助手消息在组装时复验依赖。缺失旧 manifest 或所需失效历史时
保守拒绝相应记忆上下文。原 Snapshot 保留审计，不能改写并重用旧请求。
