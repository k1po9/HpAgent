# Phase 3 — W1-A Contract Foundation

日期：2026-09-15。基线：`22f3816`。架构合同：Phase 2.2 R2.1。
本次只交付用户指定的 W1-A；W1 整包仍为 IN PROGRESS，不能开始 W2。
冻结的 phase2_2 文件保持原样，不将其 TARGET 标记改成已实现。

## 已实现合同

- `AgentRunInput`（schema v2）包含稳定 run_id/account_id、RunSource、RunContext、strategy 和执行上限。没有强制 Conversation/Session/Message、Web profile 或 Run 级 lease token。
- `RunSource.source_kind/source_ref` 引用来源 owner。Chat 的 Conversation/Session/trigger Message 引用位于可选 `ChatContext`；`RunContext.context_ref` 为其他来源保留引用边界，surface 仅为可选投影元数据。没有实现 Scheduled/File/Automation 新入口。
- `AgentExecutionInput` 在稳定输入上增加当次活跃区间的 `ExecutionLeaseRef`。后续 resume 必须构造携带新 token 的执行区间；它不能作为整个 Run 的永久身份。当前 Workflow 接收这个显式执行区间。
- Context bootstrap、model decision、tool execution、planning、evaluation 的输入均传递同一 source/context。结果仍是 transcript/result/operation 引用及版本，未引入 Model Review 快照或 UI。
- Model/tool/planning/evaluation 的 lease_token 是单次执行操作的 fencing 参数。operation_id、transcript 和 Run 身份不随 token 更新而改变。
- 现有 Chat loader/action adapter 通过 `require_chat()` 显式要求聊天上下文；通用 DTO 与 Workflow 身份校验不再要求聊天实体。完整非 Chat capability 接线尚未实现。
- Trace correlation 从实际 context 投影 conversation/session/surface，缺失时为 None，不再固定写 Web。
- lease acquire 的活跃同 owner 重投保留 token；过期同 owner、释放后重新 acquire 均增加 token。旧 token 不可续租或释放新租约。
- 更新 Web lifecycle、ReAct/Plan/step 调用者及测试夹具；审批 Dispatcher、approved tool result 同步使用合同版本常量，避免 schema v1 唤醒被 v2 Workflow 忽略。

## 权威与依赖方向

Conversation Domain 仍负责 Chat 的 Conversation、Message、Session 和 surface binding。
PG Run 是共享 Execution/Lifecycle primitive；Run 状态、operation、transcript 与 execution lease 的权威不由聊天投影或 Temporal signal 取代。当前包名、表结构不决定逻辑 owner，本次没有 package rename。
Web lifecycle 从 PG authority 取得 account/Run/Chat context 后构造执行输入；输入里的引用不授予跨 owner 权限，现有 loader 的 account/conversation/session 核验保留。
Research 仍使用独立固定 Workflow 及现有非 Chat Run shape，没有接入 Agent strategy。

## 冻结的等待合同与未完成接线

Run lifetime != Execution lease lifetime。durable wait 前必须持久化恢复点并释放 execution lease、Activity slot、workspace lock；恢复重查取消/终态、重新 acquire、新 token 传播至全部后续模型/工具及 release。Run/operation ID 保持稳定，输入依赖必须重验。Conversation admission 占位与 execution lease 相互独立。

**以上等待流程仍是本次冻结的 W1 后续实现合同，不是已实现事实。**
当前 lifecycle 仍一次 acquire 后执行整个 Agent child；文件审批尚未完成 wait 前释放和恢复新 token 的全链路接线。仅拆分输入和修复过期 acquire 不等于 suspend-safe。
剩余 W1 必须完成通用 suspend/resume、生命周期/loader/resource adapter 中立化、等待状态的 PG 映射、后续 token 传播和目标 replay/fault 验证。W5 不得承担这些基础生命周期工作。

## 验证与 Gate

最终定向测试：**130 passed，0 skipped，96.16s**。

测试文件：`test_agent_source_contract`、`test_durable_agent_contract`、`test_durable_agent_hardening`、`test_trace_events`、`test_tool_execution_approval_temporal`、`test_durable_agent_temporal_integration`、`test_durable_agent_worker_kill`、`test_action_result_semantics`、`test_web_outbox_recovery`、`test_web_temporal_contract`，以及 `test/web_persistence/` 下 `test_durable_agent_hardening`、`test_durable_agent_activity_worker_kill`、`test_f4_2_approval_temporal`、`test_persistent_web_files`。

执行方式：`.venv/bin/pytest -q` 加上述测试文件；MIGRATION_DATABASE_URL / APP_DATABASE_URL / WORKER_DATABASE_URL 指向本轮创建的独立 `postgres:16-alpine` 容器（分别使用 migration/API/worker 身份），全新执行仓库 migrations。TEMPORAL_HOST 使用本机服务，TEMPORAL_NAMESPACE 为独立 `hpagent-w1a-test-8d2cab8979`，不会与默认 namespace 的开发 worker 竞争。临时 PG 容器测试后移除；测试 namespace 的 History 保留一天。

覆盖：无 Chat 的 stable input 和 execution envelope Temporal 序列化、两 strategy 的身份校验、可替换 token 与稳定 operation、Chat trace 投影；真实 PG 过期同 Run 重获新 fence、旧 token renew/release 拒绝、释放后新 fence；既有 ReAct/Plan、worker kill、审批重启、PG Outbox signal 恢复及文件能力回归。

初次实跑 67 passed / 5 failed，发现审批调用者硬编码 schema v1；同步生产 Dispatcher 和测试 fixture 后，上述最终重跑全部通过。跳过测试的初次离线检查未用于 Gate 通过结论。

修改文件 Ruff 检查与 `git diff --check` 通过。

G03 的 W1-A 输入/序列化/调用者子集：通过。G03 完整 Gate：未通过。
G04：未通过；现有恢复测试不是新增 suspend-safe 的完成证明。
W1 总 Gate：未通过；禁止进入 W2。

未发现阻塞 W1-A 的 ARCHITECTURE DEVIATION；上述未完成项是按 W1-A 切片交付的剩余 W1 工作。

## 后续工作包

- W1 剩余：完成上述 lifecycle 与 suspend-safe 实现，G03/G04 通过并提交后才能开始 W2。
- W2：Conversation/QQ PG command + Outbox 收敛、统一 admission、delivery 重试与执行分离、双入口 G06。
- W3：满足 W2 门禁后迁出必要共享能力并删除 legacy runtime、旧会话权威和分流配置。

本次提交可用 `git log -1 --format=%H -- artifacts/architecture-audit/phase3/W1_A_implementation_report.md` 定位；最终回复提供实际 hash。用户原有 phase2_1 修改不纳入提交。
