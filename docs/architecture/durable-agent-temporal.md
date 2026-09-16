# Durable Agent Temporal 改造实施说明

## 当前实现（Phase 3 W1 + W2-A/B/C）

Web Command 在 PG 中创建 Run + Outbox，Dispatcher 固定启动 `AgentLifecycleWorkflow`。
生产 Worker 只注册这一 Agent lifecycle；W3-B 已删除 `WebRunWorkflow`、legacy
Activity、Host/Facade/loop 与旧 QQ turn Workflow。W2-A 已提取共享 Conversation
命令；W2-B 已将 QQ ingress 接入同一 PG command / Outbox / durable 链。
W2-C 已接入独立 PG delivery 状态与 QQ 投递消费者；W2-D 已通过 G06，见 W2 总实施报告。

```text
Web / QQ Command → Run + Outbox → Dispatcher → AgentLifecycleWorkflow
  prepare → source input loader → AgentRunWorkflow
    ReAct / Plan-and-Execute → segmented Activities
  finalize completed / failed / cancelled
```

### Conversation command boundary（W2-A）

`conversation_domain.commands.CommandService` 管理 Chat 的 Conversation、Message、
Session 关联，并在一个 PG 事务中创建共享 Run、预算快照和 Outbox。
Web API、Chat 终态适配器及测试工具直接调用这一实现；旧 `web_domain.services`
和 `web_domain.sessions` 路径已迁出，无重导出。Run 仍是共享 Execution/Lifecycle
primitive，Research 自己的命令和固定 Workflow 保持独立。

命令的 ownership 检查、Conversation 行锁、幂等与 admission 都使用 PostgreSQL。
发送和 retry 使用可注入的 `AdmissionPolicy`；首版 `SingleActiveRunAdmission` 与
现有 active-Run 唯一索引共同实施 single active + busy reject。Session 轮换也在同一
PG authority 上检查活跃 Run。未来队列、中断或追加策略须同步修改 PG 状态转换和
约束；仅更换 Python policy 不会自动获得这些能力。

入站键允许 UUID 或稳定的、带来源命名空间的字符串（最多 128 字符）；后者确定性
映射为 Message 的 UUID 去重标识。provider/bot/room/thread/message 的组装与授权
绑定属于 surface adapter，W2-B 已实现 QQ 入站接线。相同 account 不自动合并 Conversation。
命令结果保存产品字段；`web_api.command_projection` 生成 SSE 和附件 HTTP URL。
通用 `CommandResult` 位于 `persistence.command_result`，保留既有 PG outcome code 编码。

错误类型、失败策略、预算投影和 Outbox consumer 暂保持原物理路径；命令直接使用共享
PG repositories，不将这些独立能力迁入 Conversation。QQ ingress、持久化回复投递、
跨入口 G06 E2E 不能由 W2-A 的领域测试替代。

生命周期输入仅携带 Run ID。`ChatRunInputLoader` 校验 Chat 所属 Conversation、Session、
trigger Message，构造 `RunSource` / `RunContext`。Chat loader、资源准备、事件工厂与终态
服务由装配注入；核心 Workflow 不固定 surface/profile，也不把聊天实体作为初始参数。
`ChatExecutionBindings` 由 composition 显式注入，承接当前 Chat capability 的 Session 与 transcript 绑定；Durable Activities 不默认构造 Chat 适配器。
非 Chat 产品入口及相应数据／资源适配器尚未实现，不能将合同扩展口称为可运行的新入口。

所有 capability attempts 分别 acquire → execute → release。Retry backoff 和 durable
wait 不持有执行租约；恢复取得新 token。Run 无固定总执行超时，业务等待有独立 deadline。
分段 Activity 持续心跳以接收取消，清理后释放 segment。Workspace 仍通过共享的
`AccountLockRegistry`、Sandbox 和 PG ownership 恢复，未合并其他文件生命周期。

## 状态与持久化

Migration `014_durable_agent_control_plane.sql` 增加基础控制面，
`015_durable_agent_hardening.sql` 增加 operation intent/uncertain，
`033_agent_execution_segments.sql` 增加 segment 与 durable wait 状态，
`034_agent_transcript_run_ownership.sql` 允许无 Chat transcript，并保持 Run/account 归属约束：

- `runs.agent_strategy`：`react | plan_and_execute`；
- `agent_transcripts` / `agent_transcript_events`：模型上下文、决策和工具 raw result；
- `agent_operations`：模型、工具、计划、评估和结果的 operation idempotency；
- `account_execution_leases`：Account owner、过期时间和单调 fencing token。

Workflow History 只保存 turn、plan/step、版本、operation ID、result ref 和 compact
tool call。完整 tool arguments 通过 `arguments_ref` 从 PostgreSQL 读取；完整 context、
model content 和 tool raw result 不进入长期 History。

## Activity 策略

| Activity | 超时/重试 | 幂等键 |
|---|---|---|
| context bootstrap | 120s，最多 5 次 transient retry | `{run}:strategy:context` |
| model decision | 300s，最多 3 次 | `{run}:...:turn:{n}:model` |
| tool execution | 600s，45s heartbeat，最多 3 次 | `{run}:...:tool:{call}` |
| planning | 300s，最多 3 次 | `{run}:plan:{v}:planner` |
| plan evaluation | 60s，最多 5 次 | `{run}:plan:{v}:step:{id}:evaluation` |
| finalization / segment controls | short Activity retry policy | stable result / segment identity |

Tool Activity 在外部调用前写入 compact durable intent（tool、arguments hash、side
effect class、fencing token）。如果外部调用和 operation completion 已持久化但
Activity ack 丢失，重试直接返回之前的 result ref，不重复副作用。无法安全分类的
tool fail closed。

## 取消、恢复与版本

- Parent cancellation 使用 Temporal 默认的 child propagation；长 Tool Activity 使用
  `WAIT_CANCELLATION_COMPLETED` 和周期 heartbeat。
- Business Workflow 独占 completed/failed/cancelled terminal transaction；Agent
  Workflow 不直接更新 Run terminal state。
- 每个 workspace Activity 执行前验证并续租 fencing token；旧 token fail closed。
- canonical lifecycle schema 为 1，Agent DTO schema 为 3；新目标 histories 单独捕获并 replay，不宣称兼容 legacy History。

## 日志和 progress

所有 Activity 继续使用 `common.logging.log_event()`，关联 `run_id/account_id/
conversation_id/session_id/strategy/plan_id/plan_version/step_id/turn/operation_id/
activity_attempt`。新增 retry、dedup、lease、fencing、planning、evaluation、replan 和
synthesis 事件。Web progress 白名单增加 `planning`、`plan_ready`、`executing_step`、
`evaluating_step`、`replanning`、`synthesizing`。

## 副作用恢复边界

Workflow 控制状态支持 durable replay；Activity 以稳定 operation id 去重。只读或可
幂等副作用可以安全自动重试；进入 `intent_recorded` 后仍无法确认结果的非幂等副作用
必须先 reconciliation，无法确认时转为 `uncertain` 并 fail-closed，绝不盲目重放。
这不是对所有 Tool 的通用 exactly-once 承诺。

Chat workspace Activity 在执行分段内同时取得共享的进程内 `AccountLockRegistry`；Web
与 QQ 已通过同一个 durable lifecycle 执行，segment lease 与 Conversation admission
durable ownership。QQ 的 durable lease 接入属于后续范围。

`AGENT_EXECUTION_LEASE_TTL_SECONDS` 统一配置执行租约 TTL，生产值必须大于最长单次
Activity 超时并留出安全余量。Activity 开始时续租，并在 workspace 恢复、本地锁获取
完成后、Tool 副作用执行前再次续租和校验 fencing token。

## 已知迁移风险

1. QQ 入站使用 canonical durable runtime 的同一 PostgreSQL lease；最终回复使用独立
   delivery 状态。发送结果不确定时需显式重试，不能承诺渠道端 exactly-once。
2. 任意第三方 side-effect tool 仍需自身支持 invocation/idempotency key；operation
   intent 能覆盖“完成并持久化后 ack 丢失”，不能让不支持幂等的远端 API 变成严格
   exactly-once。
3. 仓库中的真实进程 worker-kill 验收覆盖 ReAct tool 边界和 Plan step 边界；发布流水线
   必须在真实 Temporal namespace 执行这些用例，并继续执行 cancellation propagation、
   ack-gap 和 stale fencing token 的专项用例。
4. 生产中已开始新 Workflow 后，控制流变更必须使用 Worker Versioning/patch；不要
   原地重新解释现有 History。

## 故障注入验证现状

以下为 W1-C 之前的历史实验记录；本次切换的当前验证以 W1-C 实施报告为准：

- Workflow Worker SIGKILL：覆盖 ReAct 的 model/tool 边界与 Plan-and-Execute 的 step/final
  evaluation 边界，已提交的一轮为 50/50 恢复，未观察到重复 operation 或额外重复副作用。
- Activity Worker SIGKILL：覆盖副作用前、幂等副作用 ack gap 和非幂等副作用 ack gap，已提交
  的一轮 20/20 达到预定义安全结果；其中非幂等 case 为 10/10 `uncertain` fail-closed。
- Transactional Outbox：Worker 停机期间持久化 30 个请求，replacement Worker 启动后 30/30
  最终处理，丢失 Run 和重复 Workflow 均为 0。

这些结果证明当前测试场景和环境下的 replay/retry/fail-closed 边界，不扩大为任意 Tool 的
exactly-once 保证。实验设计、复现命令和有效性限制见[基准与故障恢复验证](../benchmarks.md)，
原始证据索引见 [`artifacts/benchmarks/README.md`](../../artifacts/benchmarks/README.md)。

## QQ canonical ingress（W2-B）

`ConversationService` 只规范化 QQ 来源并调用 `SurfaceConversationCommands`。
后者在同一 PG 事务中验证绑定身份、解析 Conversation binding、检查持久化回执，
调用共享发送/取消命令，并保存 Message origin 与回执。入站回执没有待执行状态，
不是第二执行队列。外部消息键包含 channel/bot/scope/room/thread/sender/provider ID。
相同账户不自动合并不同 room；同一群内不同账户各自拥有 Conversation。

Session 使用绑定 Conversation 的 PG active Session，终态后继续复用，只有显式轮换
创建后继 Session。busy reject 为共享 admission policy；重投已拒绝消息保持拒绝。
`/cancel` 在当前账户与 Conversation 内选择 Run，回执重投不会取消后来启动的 Run。
未绑定/撤销身份与数据库失败分别返回绑定提示/暂时不可用提示。

非触发群消息仅进入可选 ambient cache；缓存失效不会改变触发判定。触发时选中的
群上下文及来源 ID 随 Message 提交 PG，执行不重新读取缓存。长期记忆只保留 PG
已提交用户消息与最终回答，群 recall 使用隔离的来源 context key。
生产不构造 QQ Host/Facade/loop/SessionStore，也不注册旧 QQ Workflow/Activity；
这些 runtime 实现已在 W3-B 删除。W2-C 已接入最终回复 delivery，W2-D 已完成 G06 验证。
验证与限制见 [W2-B 报告](../../artifacts/architecture-audit/phase3/W2_B_implementation_report.md)。

## QQ delivery（W2-C）

Chat completion 在提交 assistant Message、output file 关联及 Run completed 的同一事务
插入 `qq_deliveries`，payload 冻结结果正文、来源路由与附件引用，Run ID 唯一去重。
Web SSE 继续读取相同 Message 业务结果；不使用 QQ 的分段/引用/mention 展示实现。

`QQDeliveryService` 使用独立行锁/短 delivery lease，网络调用期间没有 PG 事务或
Agent execution lease。每段确认后推进 `next_part`；明确失败保留当前段并延迟重试。
回执超时、异常或失联 sender 记 `uncertain`，不静默当作成功或自动重复发送。
运维确认重复风险后可调用 `retry_uncertain(account_id, run_id)` 重试原段；该内部方法
不授予外部用户权限，不提供新 UI/API。晚到 sender 的状态更新受 token/state CAS 拒绝。

QQ adapter 负责 1500 字符分段、NapCat quote/@、Official msg_id/msg_seq，以及受保护
附件引用。附件不转为公开下载资源，仍需原账户在 Web 下载；本次不实现 QQ 原生文件上传。
NapCat delivery 按 self_id 选择单个连接并等待 OneBot echo 确认，避免向多个 bot 广播。
Official QQ 失败不会预先写入成功去重缓存；网络异常保留不确定状态。
完成结果投递失败不会修改 Run 状态或创建新的 start_run。失败/取消 Run 的最终通知
不在本次 completed-result delivery 范围；既有取消控制提示保留。

验证和已知限制见 [W2-C 实施报告](../../artifacts/architecture-audit/phase3/W2_C_implementation_report.md)。

W2 整包实施与 G06 证据见 [W2 总报告](../../artifacts/architecture-audit/phase3/W2_implementation_report.md)。
