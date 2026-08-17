# Durable Agent Temporal 改造实施说明

## 真实执行链与边界

改造前 Web 链路为：

```text
Outbox → WebRunWorkflow → execute_agent_activity
       → WebExecutionHost → AgentExecutionFacade
       → DefaultBrainActionLoop → BrainEngine / ActionRuntime
```

`DefaultBrainActionLoop` 同时承担 context recall/compose、tool selection、模型决策、
tool side-effect audit、工具执行、turn/max-turn 控制、forced final、heartbeat、取消和
日志。`WebRunWorkflow` 只能看到单个 Agent Activity 的 started/completed/failed。

workspace 生命周期由 `SessionResourceRecoveryService` 恢复，它通过共享的进程内
`AccountLockRegistry` 在整个 legacy `Facade.execute()` 期间持锁。这个锁不能跨
Activity 或 Worker，因此 durable 路径增加 PostgreSQL lease/fencing；进程锁在每个
workspace Activity 内仍用于本地互斥和 QQ legacy 兼容。

## 新拓扑

```text
DurableWebRunWorkflow
  ├─ prepare_run_activity
  ├─ acquire_execution_lease_activity
  ├─ AgentRunWorkflow
  │    ├─ ReactAgentWorkflow
  │    └─ PlanAndExecuteWorkflow
  │          └─ AgentStepWorkflow
  ├─ finalize_agent_result_activity
  └─ release_execution_lease_activity
```

旧 `WebRunWorkflow` 与输入 contract 保持不变。`DURABLE_AGENT_ENABLED` 只决定新
Run 启动哪个顶层 Workflow；durable definitions 始终注册，因此关闭开关不会阻断
已经开始的 durable execution。

## 状态与持久化

Migration `014_durable_agent_control_plane.sql` 增加：

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
| finalization/lease | lifecycle retry policy | stable result/lease identity |

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
- 旧 Workflow definition 和 v1 input 未修改；新 definitions 从 schema version 1 开始。

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

Web Activity 在 durable lease 外仍持有共享的进程内 `AccountLockRegistry`，因此当前
单进程部署与 QQ legacy 执行保持互斥；但 Web durable lease 并不表示 QQ/Web 已统一
durable ownership。QQ 的 durable lease 接入属于后续范围。

`AGENT_EXECUTION_LEASE_TTL_SECONDS` 统一配置执行租约 TTL，生产值必须大于最长单次
Activity 超时并留出安全余量。Activity 开始时续租，并在 workspace 恢复、本地锁获取
完成后、Tool 副作用执行前再次续租和校验 fencing token。

## 已知迁移风险与后续故障注入

1. QQ 仍走 legacy ReAct。它尚未获取 PostgreSQL durable lease，因此 Phase 3 需要将
   QQ 迁移到同一 ownership 机制，才能在多进程拓扑下与 Web 完全互斥。
2. 任意第三方 side-effect tool 仍需自身支持 invocation/idempotency key；operation
   intent 能覆盖“完成并持久化后 ack 丢失”，不能让不支持幂等的远端 API 变成严格
   exactly-once。
3. 上线前需在有 PostgreSQL/Temporal 的 CI 环境执行 worker-kill fault injection：
   model completion 后重启、tool completion 持久化后 ack 前 kill、Plan step 中断、
   cancellation propagation、stale fencing token。
4. 生产中已开始新 Workflow 后，控制流变更必须使用 Worker Versioning/patch；不要
   原地重新解释现有 History。
