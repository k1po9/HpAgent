# Phase D Temporal 测试映射

本表冻结 TD-001～TD-024 的自动化证据入口；编号与预期以
`hpagent-web-temporal-design.md` 为准。`real` 表示连接真实 Temporal Server
或 PostgreSQL，`contract` 表示确定性边界/故障分支测试。

| TD | D 任务 | 自动化证据 | 层级 |
|---|---|---|---|
| TD-001 | D-03/D-09 | `test_web_temporal_integration.py::test_td_001_real_temporal_duplicate_start_executes_agent_once` | Temporal real + History replay |
| TD-002 | D-03/D-09 | `test_web_temporal_e2e.py::test_td_002_real_db_prepare_backfills_lost_start_ack` | Temporal + PostgreSQL real |
| TD-003 | D-03 | `test_web_temporal_contract.py::test_td_003_dispatcher_does_not_start_a_run_cancelled_before_rpc`；`test_outbox_and_lifecycle.py::test_db_019_cancel_queued_before_start_is_terminal` | contract + PostgreSQL real |
| TD-004 | D-03 | `test_web_temporal_contract.py::test_dispatcher_uses_deterministic_id_and_rechecks_cancel_after_start`；`test_outbox_and_lifecycle.py::test_db_020_start_and_cancel_converge_without_duplicate_execution` | contract + PostgreSQL real |
| TD-005 | D-07/D-09 | `test_web_temporal_integration.py::test_td_005_agent_worker_sigkill_heartbeat_timeout_fails_without_retry` | 独立 Worker 进程 + Temporal real |
| TD-006 | D-02/D-09 | 同上（`failure_status=completed`） | Temporal real |
| TD-007 | D-02 | `test_web_temporal_lifecycle.py::test_td_007_cancel_and_complete_race_has_one_database_terminal` | PostgreSQL real race |
| TD-008 | D-07 | `test_web_temporal_contract.py::test_brain_action_loop_cancels_an_inflight_model_call` | contract |
| TD-009 | D-07 | `test_nsjail.py::TestNsjailExecutor::test_td_009_cancel_escalates_from_sigterm_to_sigkill` | subprocess contract |
| TD-010 | D-02 | `test_web_temporal_lifecycle.py::test_td_010_retried_lifecycle_finalizer_is_logically_idempotent` | PostgreSQL real |
| TD-011 | D-02/D-09 | `test_web_temporal_integration.py::test_td_006_td_019_real_temporal_failure_uses_domain_truth_without_retry`（`failure_status=failed`） | Temporal real |
| TD-012 | D-08 | `test_web_temporal_contract.py::test_reconciler_uses_temporal_fact_without_holding_a_domain_lock` | contract |
| TD-013 | D-08 | `test_web_temporal_contract.py::test_td_012_td_014_reconciler_converges_every_temporal_close_fact`（领域终态优先分支） | contract |
| TD-014 | D-08 | 同上（`cancelling/open` 与 `cancelling/not_found`） | contract |
| TD-015 | D-08 | `test_outbox_and_lifecycle.py::test_db_021_start_dead_letter_converges_run` | PostgreSQL real |
| TD-016 | D-06/E-05 | `test_web_temporal_contract.py::test_td_016_new_event_sink_never_reuses_a_previous_stream_id` | contract |
| TD-017 | D-01 | `test_web_temporal_contract.py::test_web_workflow_input_is_minimal_and_versioned`；`test_phase_c_context.py::test_context_refuses_cross_account_run_lookup` | contract + PostgreSQL real |
| TD-018 | D-08 | `test_outbox_and_lifecycle.py::test_db_022_terminal_dead_letter_does_not_revert_completed` | PostgreSQL real |
| TD-019 | D-01/D-09 | `test_web_temporal_integration.py::test_td_006_td_019_real_temporal_failure_uses_domain_truth_without_retry` | Temporal real；History attempt=1 |
| TD-020 | D-02/D-09 | `test_web_temporal_integration.py::test_td_020_td_021_real_temporal_cancel_uses_domain_authority`（`cancel_status=failed`） | Temporal real |
| TD-021 | D-02/D-09 | 同上（合法/意外两组参数） | Temporal real |
| TD-022 | D-07/D-09 | `test_web_temporal_contract.py::test_td_022_cancel_hostile_tool_is_detached_within_cleanup_budget`；`test_nsjail.py::test_td_022_real_cancel_hostile_child_is_killed_within_budget` | contract + 独立子进程 |
| TD-023 | D-06 | `test_context_assembly.py::test_recall_failure_degrades_to_empty_memory` | contract |
| TD-024 | D-06 | `test_context_assembly.py::test_cross_account_recall_is_a_safe_failure` | contract |

## Phase D 收口补充证据（Phase D closing additions）

Phase D 收口阶段追加的证据。层级标注沿用 D-09 规则：`real` 表示连接真实
PostgreSQL/Temporal，`contract` 表示确定性边界/故障分支测试。故障本身是模拟或
注入的必须如实标注——mock/fake 测试不称为真实故障注入。

### D-09 故障门禁追加

| 证据 | 覆盖故障 | 层级 |
|---|---|---|
| `test_outbox_and_lifecycle.py::test_db_026_dispatcher_crash_after_claim_recovers_and_dispatches_once` | Dispatcher 在 claim `start_run` Outbox 后崩溃：租约过期 → Task 1 收口恢复 → 替换 Dispatcher 以同一确定性 workflow_id 精确启动一次，无重复 Outbox、不产生第二份 start_run | PostgreSQL real（崩溃按 Outbox 契约模拟：claim 后不 mark_processed + 租约过期；恢复/再分派走真实 DB；Temporal 客户端为 stub） |
| `test_web_temporal_contract.py::test_temporal_start_temporarily_unavailable_is_retried_then_processed` | Temporal Start 暂时不可用：attempt < max_attempts 时 mark_retryable_failure（不 dead-letter），重试成功后 mark_processed | contract（Outbox/Dispatcher 为 fake，非真实故障注入） |
| `test_outbox_and_lifecycle.py::test_db_027_lifecycle_txn_failure_rolls_back_atomically` | 生命周期终态事务中途故障：整个命令原子回滚，Run 保持 running、消息保持 pending、不产生终态 Outbox 事件 | PostgreSQL real（事务/回滚走真实 DB；故障由 `patch.object` 注入） |

`test_db_026` 与 Task 1（Outbox 过期租约自动恢复）直接关联：恢复后同一
`outbox_event_id` 被替换 Dispatcher 重新 claim 并成功分派，attempt_count 递增，
`start_run` 事件始终只有一条，确定性 Workflow ID 两次一致。

### D-07 执行控制追加（Deadline 参与执行控制）

| 证据 | 覆盖 | 层级 |
|---|---|---|
| `test_web_temporal_contract.py::test_deadline_expired_prevents_model_call` | 截止时间已过期：在 recall/模型调用前以 `run_timeout` 终止，不发起模型调用 | contract |
| `test_web_temporal_contract.py::test_model_over_deadline_is_stable_model_timeout` | 模型调用越过剩余截止时间 → 稳定 `model_timeout` | contract |
| `test_web_temporal_contract.py::test_tool_over_deadline_is_cancelled` | 工具越过截止时间 → 按取消处理（CancelledError，而非 tool_timeout） | contract |
| `test_web_temporal_contract.py::test_cancel_wins_over_deadline` | 取消与截止时间同时满足：取消优先（CancelledError） | contract |
| `test_web_temporal_contract.py::test_cancellation_is_not_swallowed_by_deadline_timeout` | 模型调用中发生取消：CancelledError 不被转换为超时 | contract |
| `test_web_temporal_contract.py::test_deadline_detaches_late_model_result` | 截止时间触发后：模型迟到结果被隔离/忽略，仍映射 `model_timeout` | contract |
| `test_web_temporal_contract.py::test_deadline_during_recall_is_run_timeout` | recall/rewrite 越过截止时间 → 稳定 `run_timeout` | contract |
| `test_web_temporal_contract.py::test_qq_infinite_deadline_control_is_a_no_op` | QQ 传统 `QQLegacyExecutionControl`（deadline=`datetime.max`）对 deadline 逻辑为 no-op，不破坏 QQ 链路 | contract |

稳定错误码 `run_timeout` 已加入 `web_workflow.py::_STABLE_FAILURE_MESSAGES`（设计文档
§7.2/§8.3 已有此码），因此 Agent Activity 抛出的 `run_timeout` 会被 Workflow 判为稳定
失败，而不是 `internal_execution_error`。该改动仅新增一个 dict 条目，不影响
WebRunWorkflow 命令顺序，固定旧 History replay 门禁仍通过。

## D-09 固定旧 History 门禁（已完成）

D-09 同时保留动态 replay 与固定 fixture replay 两层门禁：

- **动态 replay**：`test_web_temporal_integration.py::test_td_001_real_temporal_duplicate_start_executes_agent_once`
  在真实 Execution 结束后立即抓取该次 History 并用当前代码 replay。作用：验证运行时抓取与
  基本 replay 能力；由于 History 与代码同版本，它无法检测未来 Workflow 修改对旧 History 的破坏。
- **固定 fixture replay**：`test_web_temporal_replay.py::test_web_run_workflow_replays_frozen_v1_completed_history`
  在完全不连接 Temporal、PostgreSQL、不启动 Docker、不依赖 `TEMPORAL_HOST` 的前提下，用当前
  `WebRunWorkflow` 代码 replay 仓库中冻结的旧 History，检测未来 Workflow 命令顺序/决策是否破坏
  历史确定性。该测试不挂 `temporal`/`postgres` marker，属于离线单元门禁。

固定 fixture：

- 路径：`test/fixtures/temporal/web_run_workflow_v1_completed.json`
- 来源：真实 Temporal Server 上完成的最小 happy-path `WebRunWorkflow` Execution，完整 History
  （17 个事件），非仅 close event。
- 捕获方式：`scripts/capture_web_workflow_history.py` 连接 `TEMPORAL_HOST`，注册 stub
  activities，启动一次真实 Execution，`handle.fetch_history()` + `to_json()` 直接写入 fixture，
  写盘后立即从磁盘重新读取并离线 replay 验证；目标文件已存在时默认拒绝覆盖，仅显式 `--force`
  允许重捕。详细来源与更新规则见 `test/fixtures/temporal/README.md`。
- 捕获基线：Git commit `e542ef5a2e079fa543d55a9b0ceade930f050f9c`，Temporal Server 1.26.2，
  Temporal Python SDK 1.31.0，workflow_id `hpagent-web-run-a423e6b8-17b8-4446-8ed2-873152ae8329`。

CI 门禁：

- `phase-d-temporal-fault-gate` job 在真实 Temporal 套件之前新增 step：
  `Phase D frozen Workflow History replay`，运行
  `PYTHONPATH=.:src python -m pytest test/test_web_temporal_replay.py --junitxml=test-results/phase-d-replay.xml`。
  该 step 无 `continue-on-error`、无 flaky retry，replay 失败即 CI 失败；CI 从不生成或刷新 fixture；
  JUnit 输出独立为 `phase-d-replay.xml`，不覆盖 `phase-d.xml`。该离线测试同时被
  `existing-unit-tests`（`-m "not postgres" test`）自然收集执行。

本地验证结果（与本表一致，非伪造；最新一次本地全量验证，Phase D 收口后）：

```text
env -u TEMPORAL_HOST PYTHONPATH=.:src python -m pytest -q test/test_web_temporal_replay.py
结果：1 passed

env -u TEMPORAL_HOST PYTHONPATH=.:src python -m pytest -m "not postgres" test
结果：240 passed / 6 skipped（Temporal 集成无 TEMPORAL_HOST 跳过）/ 86 deselected

MIGRATION/APP/WORKER_DATABASE_URL=...hpagent_phase_d_closing... \
  PYTHONPATH=.:src python -m pytest -m "postgres" test
结果：85 passed / 1 skipped（Temporal 集成 e2e 无 TEMPORAL_HOST 跳过）/ 246 deselected
```

Temporal 真实集成（`TEMPORAL_HOST=localhost:7233` 的
`test_web_temporal_integration.py`/e2e/lifecycle/replay，此前结果 10 passed）需要
Temporal Server 运行；本地无 TEMPORAL_HOST 时按门禁约定跳过，不纳入上述计数。

## Phase D 完成状态

- **D-01～D-09 技术实现已完成**：TD-001～TD-024 均已映射到自动化证据，D-09 固定旧 History 门禁已
  通过真实捕获、离线 replay 与 CI 接入完成。
- **D-00 正式设计评审待人工批准**：`hpagent-web-temporal-design.md`（Temporal 0.2）仍为“待评审”，
  仓库内不存在已批准的本地评审证据。在人工批准前，Phase D 不能正式宣告完成；`phase-d-test-map.md`
  与实施计划 §14 的“Temporal 0.2 是否在 Phase D 完成前升级为已评审”均为实施计划自检项，不是评审批准记录。

## Agent/QQ 融合证据

- D-04：`test_web_temporal_contract.py` 覆盖 Facade/ReplySink 分离、每 execution
  工具缓存分区、两阶段 recall、Audit degraded、模型/工具取消与迟到结果隔离。
- D-05：`test_qq_execution_compat.py` 覆盖默认关闭且可配置的 feature flag、每轮只
  选择一次且失败不回退、稳定 execution_id 重投、metadata 白名单、QQ Context/Audit/
  Event/Reply/retain adapters、群进度、智能 @ 与 Channel 失败兼容。
- D-06：`test_web_temporal_contract.py` 与 `test_context_assembly.py` 覆盖按 run_id
  服务端归属加载、Redis degraded、Sink close fence、成功提交顺序和记忆隔离。
