# Phase 3 — W1-B Suspend / Resume Lifecycle + Segmented Lease

日期：2026-09-15。基线：`c0e718a`（W1-A）。架构合同：Phase 2.2 R2.1。
范围：W1-B；没有进入 W2/W3/W5/W6，没有修改冻结的 phase2_2。

## 已实现

1. `AgentRunWorkflow` / ReAct / Plan / Step 只持有稳定 `AgentRunInput`。Web 生命周期改为读取身份后启动 Agent，不再跨整个 child acquire/release lease。输入升级 schema v3，启动时 execution envelope 被分段控制合同替代，来源合同保持中立。
2. `execute_segment` 为一次 capability Activity attempt 获取当前 fencing token，替换待执行输入中的占位 token，并在成功、失败、取消后释放。覆盖 bootstrap、model、tool、planning、evaluation、replan、step、forced final、synthesis、approved tool。每次重试用新 segment，新 token；逻辑 operation/transcript ID 不变。
3. Temporal 自动 capability 重试改为单次 attempt，由 Workflow 在 release 之后用 timer backoff，再 acquire。预算/provider attempt 使用显式 execution_attempt，避免所有 attempt 被误记为 1。短事务控制 Activity 的重试保持幂等。
4. 新 `agent_execution_segments` 保存 requested/active/released，账户 lease 记录 owner_segment_id。活跃 segment 的 acquire 重投返回同 token；过期后重获新 token；released segment 不能复活。按 segment ID 清理可覆盖 acquire 已提交但响应丢失；旧 segment 清理不能释放新 segment。
5. `DurableWait` 与 PG `agent_run_waits` 保存 operation、reason、resume_ref、deadline 与 waiting/resumed/cancelled/expired。信号只是通知；probe 读取业务权威决定是否恢复。带 probe 的等待适用于 tool approval、未来 Model Review、external callback、human confirmation；无 probe 的 deadline 等待适用于 timer/rate limit。未实现这些未来来源的产品 API。
6. Tool Approval 使用该等待机制。初始工具 Activity 完全返回且 segment 已释放后才进入等待；批准后的工具是新的执行 segment。重复/错误唤醒不授予执行权限，丢失通知可由短 probe + Workflow timer 恢复，等待期间无活跃 Activity。
7. 新 acquire 在 PG 中检查 account/Run 状态与正在等待的记录；取消/终结 Run 不可恢复。capability store 的短事务通过 fence scope 核验并锁住 Run/lease，再进行读写/CAS，迟到 token 无法提交 operation/transcript。现有工具与 persistent overwrite 的副作用前 fencing 检查保留；模型取得 workspace lock 后再次核验，bootstrap 也续租。
8. Activity 和 child 取消等待其执行结束后清理；等待中的取消关闭 PG wait。已 cancelled 的 Web Run 直接走终态清理，不再等待一个永不到达的信号。durable Run 不继承 legacy Web 的单次执行总超时；每个 wait 使用业务 deadline。
9. 增加可复现的隔离验证脚本 `scripts/verify_w1b_contracts.py`：临时 PG 容器 + 新 Temporal namespace，独立 API/worker 角色，从空 schema 执行 migrations；不清理现有开发应用库。

## 生命周期与状态映射

Run lifetime 独立于 execution segment。Run 状态继续由现有 PG lifecycle 管理；named business wait 的 phase/reason 在 `agent_run_waits` 表，不扩展 runs.status 为产品状态全集。等待中的 Conversation admission 仍按现有 PG policy 占位；同账号另一个合法 Run 可在此时取得 execution lease。busy acquire 与 retry 的 timer 恢复点在 Temporal History，业务 wait 另有 PG 记录。

release 必须完成后才进入等待。等待不持有 DB transaction、workspace/account lock、Activity slot、执行 lease 或模型 reservation。新的 acquire 只授予当前 segment 权限，不沿用父 Workflow 的旧 token。新 token 经 Activity 输入实际传递，批准后的工具以及后续策略步骤均走同一 helper。

W5 可以直接调用 `DurableWait.run(WaitInput(...), authoritative_probe)`，由 Model Review 的 probe 校验快照/授权，返回后调用 `execute_segment`；不需要再次改基础 lease lifetime。该基础设施本身不构成 Model Review 授权实现。

## 验证

验证命令：`.venv/bin/python scripts/verify_w1b_contracts.py`。

最终统一验证：**145 passed / 0 skipped，172.58s**。本轮临时 namespace 为 `hpagent-w1b-test-99c8208f2b`，包含真实 PG/Temporal、新离线 replay 与新增终态断言。临时 PG 容器已移除，测试 namespace 中运行中的测试任务已清理；已关闭的 History 保留一天。

恢复 benchmark 的 Workflow/Activity DTO 调用者也同步到 source/context 合同，另完成 2 个输入 DataConverter roundtrip；未运行整套性能 benchmark，不将输入检查宣称为性能验证。

核心新增 PG/Temporal 场景覆盖：

- lease 到期、same-segment acquire 响应丢失重投、reacquire 新 token；旧 token renew/release/结果提交被拒绝；旧 segment cleanup 不影响新 segment，released tombstone 阻止复活。
- 跨 TTL 的 external wait、停掉并重建 Workflow worker、重复/错误通知、读取 PG 权威再恢复；只配置一个 Activity slot，同账号另一 Run 在等待期间完成，workspace guard 未持有且 PG 没有 idle-in-transaction。
- acquire/release 已提交但响应丢失、capability 提交后崩溃重试，稳定 operation 复用已提交结果，实际输入 token 与 execution_attempt 更新。
- acquire 响应延迟至 token 已过期，Activity 拒绝旧输入，Workflow 用新 token 重试。
- Workflow cancel 和 PG cancelling 都阻止等待后执行；活跃 Activity cancellation 等资源关闭后释放；完成和取消 History 均通过 Replayer。
- 原有 ReAct/Plan、真实 OS worker kill、工具副作用恢复、PG File Approval + Outbox signal、persistent file、Trace、dispatcher 回归。

离线新基线在 `test/fixtures/phase3/`：ReAct 68 events、Plan 113 events、Tool Approval 71 events。使用生产 Workflow 类型 replay；模型/工具是测试替身，PG fencing 行为由独立真实 PG/Temporal 场景证明，未冒充 legacy History 兼容。

Ruff 与 `git diff --check` 通过。

## Gate 与范围

**W1-B Gate：通过（VERIFIED）。**W1 整包仍不得仅凭本报告判为完成；G03/G04 的整个 canonical Web 入口/装配/取消/预算/策略行为还需结合剩余 W1 收敛统一验收。W2/W3 未开始。

W1-A 报告是该提交时点的记录；其中“suspend/resume 尚未接线”已由本次实现取代，不反向改写历史证据。无阻塞本工作包的 ARCHITECTURE DEVIATION。

既有外部副作用边界不变：fencing 阻止过期 Activity 发起后续受保护操作及提交结果，不能撤回 provider 已接收的请求，也不声称第三方系统 exactly-once；不确定工具效果仍走既有 reconciliation/uncertain 合同。Activity crash 后在 lease TTL 内可能暂时占 lease，但不能跨显式 durable wait 保持 lease。

尚未实施：QQ/Conversation 收敛（W2）、legacy loop/旧状态权威与分流退役（W3）、完整 source adapter 中立化等剩余 W1 验收、ModelInputSnapshot/Review（W5）及 Workspace 查询/UI（W6/后续）。

提交由 `git log -1 --format=%H -- artifacts/architecture-audit/phase3/W1_B_implementation_report.md` 定位；最终答复给出实际 hash。用户原有 phase2_1 修改不纳入提交。
