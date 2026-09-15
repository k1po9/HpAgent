# Phase 3 · W1 Canonical Durable Runtime — Implementation / Gate Report

日期：2026-09-15。状态：**W1 Gate PASSED / VERIFIED（W1 范围）**。
架构合同：冻结的 Phase 2.2 R2.1；没有修改其历史事实或把未实施目标反向标记为已实现。

## Commit 基线与完成点

| 字段 | Commit |
| --- | --- |
| baseline commit（W1 开始前，Phase 2.2 R2.1） | `22f38161280c03d230bb259cf7b3f4153b9a05a6` |
| W1-A contract foundation | `c0e718a33f34ba60995d33844492bac9b5036cb3` |
| W1-B segmented lease / suspend-resume | `939f39febcb32f35ad9e68432cd2f2b296f295c2` |
| W1-C Web canonical cutover / W1-D 审核起点 | `590894e74f7acf157d985a842c72575837f1e69b` |
| **final commit（最终实现与测试）** | **`e0dfc7a4a8bc75d5f8c475f5bccf29b179da8288`** |

本报告与验证输出在上述 final implementation commit 之后单独提交，不再修改实现。
报告提交可由 `git log -1 --format=%H -- artifacts/architecture-audit/phase3/W1_implementation_report.md` 定位。
这样 final commit 字段指向已存在、实际验证过的代码，而不是无法在报告内预知的自身 Git hash。

## Changed architecture

```text
Web Command
  → PostgreSQL Run + Outbox
  → Dispatcher（同一确定性 Workflow ID）
  → AgentLifecycleWorkflow
  → AgentRunWorkflow
  → ReAct / Plan-and-Execute / AgentStep / ToolExecution
  → durable capability Activities

每次 capability attempt：acquire segment → 当前 fencing token → execute → release
retry backoff / durable wait：Workflow timer / notification，没有长期执行租约
resume：核验 Run authority → reacquire → 新 token → 后续 capability attempt
```

1. Run 是共享 Execution/Lifecycle primitive；source/context 来自其创建者。Conversation/Message/Session 是 Chat domain 的上下文，已不再是 AgentRunInput 的必填顶层字段。AgentRunInput 不含 lease token 或固定 Web profile。
2. 原 durable Web lifecycle 演进为 `AgentLifecycleWorkflow`，输入仅为 schema + Run ID。Web 新 Run 无分流开关，Dispatcher 只接受 canonical Workflow owner，生产 registry 不再注册 legacy Web Workflow/Activity，也不构造 legacy Web Host/loop。
3. Run source loader、上下文绑定、workspace/resource adapter、事件工厂与终态服务由 composition 注入。W1-D 将 `ExecutionContextBindings` 改为必需的注入合同，移除核心 runtime 默认构造 Chat 绑定；共享终态 Trace 不再硬编码 Web；删除无生产 caller 的 profile→strategy Web 别名 helper。
4. Transcript 属于 Run/account。migration 034 使 Conversation/Session 可空，保留原 Chat FK，增加不依赖可空 Chat 字段的 account/Run FK；store 仍核验传入 context 与 Run authority 一致。用现有 file_job schema shape 的隔离合同夹具证明，无需创建 Conversation 或伪造 Session。没有新增 File-triggered/Scheduled 产品入口，也没有将 Research 接入 AgentRunWorkflow。
5. W1-B 的 segment ID、递增 fencing token、迟到写入校验、短事务与 durable wait 继续生效。初始 Activity 请求中的 token=0 只是待派发占位；`execute_segment` 在每次 attempt 派发前用 acquire 的有效 token 替换。Run 可在无 lease 状态长期存在。
6. `agent_run_waits` 保存 operation/reason/resume_ref/deadline/state；Run 的 PG status 仍按已有 lifecycle 管理，等待不取消 Conversation admission 占位。Admission policy 与 execution lease 是不同合同。Tool Approval 已消费该机制，timer/callback/model_review 原因的验证使用同一 helper。
7. Complete/failure/cancel 由 PG 终态服务决定；迟到成功/失败不能覆盖已 cancelled 的 Run。模型/工具 capability 具有心跳，取消等待 Activity 清理后释放 segment。Start ack 丢失和 Outbox 重投恢复使用同一 Workflow ID，不重新创建 Agent execution。
8. W1-D 将 Temporal connect、Web composition 等初始化完成后的启动阶段纳入 shutdown 清理范围，验证失败时释放共享资源；不新增资源副本。Research、Artifact、Heavy Document 保留原有独立 Workflow/worker/发布职责。

## 四项阻断检查

| 问题 | 最终答案 | 证据与界限 |
| --- | --- | --- |
| 新的核心 Agent Runtime 是否仍写死 Web？ | **否** | context bindings 显式注入、source-neutral input、Trace 无固定 surface、无分流。`hpagent-web-agent` / `hpagent-web-lifecycle` 是保留的队列地址，`web_domain` 是当前 package 名，不决定 Run source；未为名称好看而改队列/包。Web loader/profile/SSE 仅位于显式 Chat/surface adapter。 |
| Run lifetime 是否仍等于 lease lifetime？ | **否** | 跨 lease TTL 等待、停止并重建 worker、同 account 另一 Run 在单 Activity slot 下完成；等待期 lease owner 为空、workspace lock 未持有、无 idle DB transaction；恢复 token 增加。 |
| AgentRunInput 是否仍携带永久固定 token？ | **否** | input 字段合同与 DataConverter roundtrip；模型/工具请求按 segment 注入 token；旧 token 的续租/读取/结果提交均被拒绝。 |
| W5 接入 Model Review 是否需要重设 lifecycle？ | **否** | `DurableWait.run(WaitInput, authoritative_probe)` 已支持 reason/resume_ref/通知/超时/取消，后续使用 `execute_segment` 重获租约；model_review 原因通过真实 PG/Temporal 重启等待测试。W5 仍需实现 Prepare/Freeze/Authorize/Invoke、snapshot/hash/授权策略与依赖复核，但不用改变 Run/segment/wait 基础合同。 |

上述 model_review 测试只证明 W5 所需等待基础设施，不是 ModelInputSnapshot、审阅授权或 UI 的实现证明。

## Tests / 运行证据

原始摘要保存在 [W1_validation_results.txt](W1_validation_results.txt)。本轮使用临时 PostgreSQL 容器、独立 API/Worker 角色、新 Temporal namespace，并从空 schema 执行 migrations。没有使用或清空现有应用数据库。

| 执行 | 结果 |
| --- | --- |
| `.venv/bin/python scripts/verify_w1_gate.py` | **207 passed / 0 skipped，329.99s**；namespace `hpagent-w1b-test-eae8fc1716` |
| `.venv/bin/python scripts/verify_w1_gate.py test/test_durable_agent_temporal_integration.py -k replan` | **2 passed / 3 deselected / 0 skipped，11.11s**；namespace `hpagent-w1b-test-4383d8375e` |
| 新增 source data-plane / startup failure 定向检查 | **5 passed / 0 skipped，15.47s**；已包含在 207 项中，不重复计数 |
| Ruff（本轮变动 Python）与 `git diff --check` | **PASS** |

两个 replan 用例在统一测试 collection 后加入，因此单独执行；本次 Gate 共 **209 个不同用例通过**。统一脚本在最终代码中也包含这两个用例。唯一 warning 是既有 Starlette/httpx 弃用提示，不影响 Gate。

| 必需覆盖 | 主要证据 |
| --- | --- |
| Agent contract / 非 Chat context | `test_agent_source_contract.py`、`test_w1c_cutover_contract.py`、`web_persistence/test_w1_source_data_plane.py` |
| canonical registry / API config / caller | `test_durable_agent_contract.py`、`test_web_temporal_contract.py`、`web_api/test_config.py`；生产 composition 静态核对 |
| lifecycle / Run status / complete / failure / cancel | `web_persistence/test_web_temporal_e2e.py`：两 strategy 的真实 PG complete/failure/cancel；`test_web_temporal_integration.py`：已终态/取消中不再启动 child；`test_w1c_cutover_contract.py`：迟到结果竞争 |
| suspend/resume / lease expiry / reacquire / fencing | `web_persistence/test_agent_segments.py`、`test_agent_segment_temporal.py`：真实 PG authority、TTL、旧 token、lost response、重复/错误通知与 worker restart |
| 零长期等待资源 / W5 wait 接口 | `test_agent_segment_temporal.py`：external_callback / model_review，单 Activity slot、同 account sibling、无 lease / workspace lock / idle transaction；timer 使用同一 helper |
| retry / 工具副作用与 uncertain / 预算 | `test_durable_agent_hardening.py`、`web_persistence/test_durable_agent_hardening.py`、`test_action_result_semantics.py`；稳定 operation ID 与 retry attempt |
| worker crash | `test_durable_agent_worker_kill.py`：lifecycle 与 Agent Workflow 进程 SIGKILL；`web_persistence/test_durable_agent_activity_worker_kill.py`：真实 Activity 进程中断/重投 |
| Temporal replay | 五份独立目标 history 离线 replay；PG 等待/取消、Web 终态、SIGKILL 恢复及 replan/max-replan history 取回后 replay |
| Outbox duplicate start | `web_persistence/test_web_temporal_e2e.py`：Start 响应丢失、重投、已结束 ID 再 Start；`test_w1c_cutover_contract.py`：running Run 补记录、拒绝其他 Workflow owner |
| ReAct / Plan / step / synthesis / replan | `test_durable_agent_temporal_integration.py`、真实 Web E2E；新增 replan 验证 previous plan/version、completed refs、独立 operation IDs、三次重规划上限 |
| G02 资源与启动失败 | `test_w1_gate_resources.py`：connect/compose 失败释放资源、取消锁等待者；`test_workspace_isolation.py`、`test_workspace_provisioning.py`、`test_web_background_tasks.py`、`test_web_outbox_recovery.py` |
| File approval 与持久文件回归 | `test_tool_execution_approval_temporal.py`、`web_persistence/test_f4_2_approval_temporal.py`、`test_file_action_approvals.py`、`test_persistent_web_files.py` |

真实外部模型、第三方工具和 Sandbox 在 E2E 中使用受控替身；PG、Temporal、Git/workspace 恢复与进程终止按测试范围真实运行。不声称 provider exactly-once，不把这些测试称为性能 benchmark 或全新镜像部署验证。

## Gate results

| Gate | W1 结论 | 范围 |
| --- | --- | --- |
| G01 文档/范围 | PASS（本次交付） | 冻结 phase2_2 不变；当前文档与报告区分事实/未来目标；用户原有 phase2_1 修改未纳入提交。 |
| G02 组合/资源 | **PASS（W1 canonical 路径）** | 显式注入、共享资源、single-process 锁、启动失败/取消清理、消费者恢复与注册。 |
| G03 目标合同 | **PASS（W1）** | 非 Chat 合同/数据面、无永久 lease token、唯一 Web canonical registry、移除 durable 分流。两 surface 的领域/权限 E2E 留给 W2/G06。 |
| G04 Durable 行为 | **PASS（W1）** | 本报告上述 209 个通过用例覆盖所有要求项及重规划/取消竞争。 |
| G05 / G06 | NOT IMPLEMENTED / 不在 W1 退出条件内 | 分别由 W3 legacy retirement、W2 Conversation/QQ convergence 交付；不能宣称双入口目标完成。 |
| G07–G13 其余后续 Gate | 本报告不作整体通过声明 | 当前 W1 只复用相关回归；不实现 worktree、产品 UI、Model Review、WorkspaceQuery，也不冒充全新镜像安装验收。 |

**结论：W1 完成，已具备进入 W2 的前置 Gate。当前 Session 不进入 W2。**

## Deviations / 审核中修复的问题

没有架构决策偏离；无需 ARCHITECTURE DEVIATION。W1-D 找出的实际 implementation gaps 已在 final commit 修复：

- 共享 Trace 的固定 Web metadata；
- runtime 隐式创建 Chat bindings；
- transcript 的 Chat NOT NULL 和缺少独立 account/Run ownership 约束；
- 已初始化依赖在 Temporal connect/Web compose 失败时不进入清理范围。

修复没有改变冻结的 Run/Conversation owner 或 suspend/resume 目标。仅迁出/显式注入必要合同，不执行 W3 的整体 legacy 删除，不合并 Research/File/Document/Workspace/Artifact 生命周期。

## Remaining W2 items

1. QQ ingress 调用统一 PG Conversation 命令，以 provider/目标/message identity 完成入站幂等、Conversation binding、Session 与 admission authority；不再使用私有 mailbox 作为第二权威。
2. QQ 通过同一 Run + Outbox → canonical lifecycle，注入 QQ Chat context/profile/resource/event adapter，覆盖身份未绑定与 DB 失败的不同语义。
3. QQ 投递从已提交结果派生，投递失败仅重试 delivery，不能重执行已完成 Agent；保留群/私聊隔离、引用/@/附件和必要长期记忆语义。
4. 验证 G06：双 surface 同一主线、busy/轮换/取消/retry、跨 surface ownership 与重复消息、回复及记忆来源。
5. W2 Gate 通过且 commit 后才能进入 W3。W3 再删除 legacy Host/loop/Workflow、QQ WAL/SessionStore 权威以及仅供旧实现的测试/合同；本次没有提前执行。

## Changed files

以下为 baseline → final implementation commit 的完整已提交文件清单（含 W1-A/B/C/D 与各自报告）；本总报告和原始验证摘要由后续报告提交添加。`M` 修改，`A` 新增，`D` 删除，`R` 重命名。

```text
M	.env.example
M	README.md
A	artifacts/architecture-audit/phase3/W1_A_implementation_report.md
A	artifacts/architecture-audit/phase3/W1_B_implementation_report.md
A	artifacts/architecture-audit/phase3/W1_C_implementation_report.md
M	docker-compose.yaml
M	docs/architecture/durable-agent-temporal.md
M	docs/architecture/single_agent/03_component.md
M	docs/operations/file-assistant-f2-runtime-acceptance.md
M	docs/reference/configuration.md
M	docs/web/hpagent-file-workspace-validation-deployment-runbook.md
A	persistence/migrations/033_agent_execution_segments.sql
A	persistence/migrations/034_agent_transcript_run_ownership.sql
M	scripts/benchmarks/agent_strategy/README.md
M	scripts/benchmarks/agent_strategy/agent_strategy_experiment.py
M	scripts/benchmarks/outbox/outbox_benchmark_worker.py
M	scripts/benchmarks/temporal/activity_worker_recovery_benchmark.py
M	scripts/benchmarks/temporal/temporal_recovery_benchmark.py
M	scripts/capture_web_workflow_history.py
A	scripts/verify_w1_gate.py
A	scripts/verify_w1b_contracts.py
A	scripts/verify_w1c_contracts.py
A	src/agent_activities/context_contracts.py
A	src/agent_activities/fencing.py
M	src/agent_activities/persistent_overwrite.py
M	src/agent_activities/runtime.py
A	src/agent_activities/segments.py
M	src/agent_activities/store.py
A	src/agent_execution/activity_control.py
A	src/agent_execution/chat_bindings.py
A	src/agent_execution/chat_run_input.py
M	src/agent_execution/tracing/lifecycle.py
M	src/agent_execution/web_adapters.py
M	src/agent_workflows/agent_step.py
M	src/agent_workflows/contracts.py
A	src/agent_workflows/ids.py
A	src/agent_workflows/lifecycle_contracts.py
M	src/agent_workflows/plan_execute.py
M	src/agent_workflows/react.py
A	src/agent_workflows/segments.py
M	src/agent_workflows/tool_execution.py
M	src/file_domain/approvals.py
M	src/file_runtime/routing.py
A	src/orchestration/agent_lifecycle_workflow.py
M	src/orchestration/artifact_workflow.py
M	src/orchestration/config.py
D	src/orchestration/durable_web_workflow.py
M	src/orchestration/research_schedule.py
M	src/orchestration/research_workflow.py
A	src/orchestration/run_lifecycle_activities.py
A	src/orchestration/run_lifecycle_contracts.py
M	src/orchestration/web_activities.py
M	src/orchestration/web_dispatcher.py
M	src/orchestration/web_workers.py
M	src/orchestration/web_workflow.py
M	src/orchestration/worker.py
M	src/web_api/app.py
M	src/web_api/config.py
M	src/web_domain/services.py
A	test/fixtures/phase3/README.md
A	test/fixtures/phase3/lifecycle_plan_and_execute_completed.json
A	test/fixtures/phase3/lifecycle_react_completed.json
A	test/fixtures/phase3/plan_segments_completed.json
A	test/fixtures/phase3/react_segments_completed.json
A	test/fixtures/phase3/tool_approval_segments_completed.json
M	test/support/durable_worker_process.py
M	test/support/research_worker_process.py
A	test/support/segment_activities.py
A	test/support/segment_workflows.py
A	test/test_agent_segment_replay.py
A	test/test_agent_source_contract.py
M	test/test_durable_agent_contract.py
M	test/test_durable_agent_hardening.py
M	test/test_durable_agent_temporal_integration.py
M	test/test_durable_agent_worker_kill.py
M	test/test_research_temporal_integration.py
M	test/test_research_worker_kill.py
M	test/test_tool_execution_approval_temporal.py
M	test/test_trace_events.py
A	test/test_w1_gate_resources.py
A	test/test_w1c_cutover_contract.py
M	test/test_web_temporal_contract.py
M	test/test_web_temporal_integration.py
M	test/web_api/test_config.py
A	test/web_persistence/test_agent_segment_temporal.py
A	test/web_persistence/test_agent_segments.py
M	test/web_persistence/test_durable_agent_activity_worker_kill.py
M	test/web_persistence/test_durable_agent_hardening.py
M	test/web_persistence/test_f4_2_approval_temporal.py
M	test/web_persistence/test_file_action_approvals.py
M	test/web_persistence/test_persistent_web_files.py
M	test/web_persistence/test_research_schedule_temporal_integration.py
A	test/web_persistence/test_w1_source_data_plane.py
M	test/web_persistence/test_web_temporal_e2e.py
```
