# Durable Agent Temporal 改造实施说明

## 当前实现（Phase 3 W1-D）

Web Command 在 PG 中创建 Run + Outbox，Dispatcher 固定启动 `AgentLifecycleWorkflow`。
生产 Worker 只注册这一 Agent lifecycle；`WebRunWorkflow`、legacy Activity 与 Host
源码暂留 W3 退役，已无 Web 生产入口或注册。QQ 仍使用旧链，W2 尚未实施。

```text
Web Command → Run + Outbox → Dispatcher → AgentLifecycleWorkflow
  prepare → source input loader → AgentRunWorkflow
    ReAct / Plan-and-Execute → segmented Activities
  finalize completed / failed / cancelled
```

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

Web workspace Activity 在执行分段内同时取得共享的进程内 `AccountLockRegistry`，因此当前
单进程部署与 QQ legacy 执行保持互斥；但 Web durable lease 并不表示 QQ/Web 已统一
durable ownership。QQ 的 durable lease 接入属于后续范围。

`AGENT_EXECUTION_LEASE_TTL_SECONDS` 统一配置执行租约 TTL，生产值必须大于最长单次
Activity 超时并留出安全余量。Activity 开始时续租，并在 workspace 恢复、本地锁获取
完成后、Tool 副作用执行前再次续租和校验 fencing token。

## 已知迁移风险

1. QQ 仍走 legacy ReAct。它尚未获取 PostgreSQL durable lease，因此 Phase 3 需要将
   QQ 迁移到同一 ownership 机制，才能在多进程拓扑下与 Web 完全互斥。
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
