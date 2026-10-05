# HpAgent Durable Work V1 五阶段实施复查

> 历史设计/复查基线：本文保留当时状态与证据。当前已实现架构见 [Durable Work V1](../architecture/durable-work-v1.md)，阶段演进和后续人工修复见[实施索引](../implementation/README.md)。
> 源码链接已改为仓库相对路径，行号沿用原研究基线；退休文件以路径文字保留，不链接到不存在的当前文件。


日期：2026-10-02。审查对象：`main@0ec0763`，相对于 `3e6379f` 的五个实施提交。

后续修复与验证：[2026-10-03 修复记录](../implementation/durable-work-v1-review-fixes-2026-10-03.md)。本报告保留复查当时的发现；修复状态以该记录为准。

结论：核心领域拆分已经落地，但不能判定五阶段全部验收完成。本次确认了五类运行缺陷，另有八项选定后端测试失败和现有部署尚未切换的问题。无需据此推翻 Main / Work / Run 架构；应先修复关键链路并完成原设计的退出验收，再继续扩展能力。

本轮没有修改生产代码、测试源码、现有业务数据库，也没有提交 commit。仅新增本报告；最小复现脚本位于 `/tmp/hpagent_durable_review.py`。诊断使用独立临时 PostgreSQL、真实 API/worker 数据库角色，以及现有 Temporal 上的独立测试队列。没有调用真实模型或向 QQ 发送消息。

## 1. 已确认的运行缺陷

### R1 · P1：QQ 群聊的 Work 工具丢失受众边界，能够取得私有委托详情

位置：[ExecutionResourceService._sandbox](../../src/workspace/execution.py#L71)、[Main Work tools](../../src/application/main_agent.py#L44)。

实际链路是 Message.origin → ExecutionResourceService → SandboxManager → create_main_work_tools。QQ 入口已经在 origin 保存 `scope=group` 和 `interaction_profile=qq_group`，但 `_sandbox` 只传递 `channel_type=napcat/official_qq`，没有传递 profile/scope。`get_work` 恰好依赖被丢弃的 profile/surface 决定是否裁剪，因此真实群聊执行走完整详情分支。`list_works` 还会无条件提供账户全部候选 Work 的标题。

复现：在隔离库创建含 `PRIVATE_WORK_OBJECTIVE` 的私有 Work，以及 origin 明确为 QQ 群聊的 Message；通过真实 `_sandbox` 构造工具上下文，再调用真实 `get_work`。返回包含该私有 objective；`list_works` 也包含私有标题。使用捕获型 sandbox 替代外部执行环境，未调用模型或真实群聊。

风险是同一用户跨公开/私有入口的信息披露，不是已证明的跨账户越权。模型一旦将这些详情写入群聊回答，后续投递不会重新裁剪： [DeliveryService](../../src/delivery/service.py#L344) 将完整 assistant content 同时作为 summary，QQ adapter 会发送该 summary。

修复方向：受众信息应由可信 Message origin 贯穿工具边界；群聊候选发现、详情和变更命令的返回值统一使用公开投影，而不能仅修补 `get_work` 的一个判断。不能以“同一 Account 有权读取”代替“当前群聊有权披露”。

回归要求：真实 QQ ingress → execution resources → tools 的整条边界测试；群聊不能取得未授权公开的标题、objective、checkpoint、Artifact/资源详情，Web/私聊仍可读取账户内授权内容。

### R2 · P1：成功重试之后的下一次执行被错误挂到同一个失败 Run，触发唯一约束并中断调度

位置：[admit_work_run](../../src/run_domain/admission.py#L73)、[Work scheduler](../../src/orchestration/work_schedule.py#L134)。

每次 admission 都查询该 revision 最近一个 failed/cancelled Run，并将其作为 `retry_of_run_id`，没有判断它是否已经被重试成功，也没有区分新的履约步骤/定时 occurrence 与重试。最终 schema 仍保留 [uq_runs__one_direct_retry](../../persistence/migrations/001_phase_a_schema.sql#L114)。

复现序列：R1 failed → R2 retry_of=R1 且 succeeded，Work 仍 active → 再次 advance。R3 再次指向 R1，抛出 `UniqueViolation: uq_runs__one_direct_retry`。相同情况放到 pending wakeup 后，`dispatch_due()` 也直接抛出该异常。

影响包括 ongoing Work 和周期工作失败恢复后的后续执行。异常不在 `dispatch_due` 的捕获范围；`run_work_schedule_loop` 没有异常恢复，而 [BackgroundTasks](../../src/orchestration/worker.py#L333) 只保存任务，没有重启监督。因此异常会结束该进程的 Work 调度任务，其他租户的后续唤醒也受影响。

修复方向：只有明确的 retry 才设置 retry ancestry；新步骤和新 occurrence 不沿用历史失败祖先。保留必要的并发幂等约束，补充单 Work admission 异常隔离与调度任务健康/恢复机制。仅删除唯一索引不能修复错误的执行因果关系。

回归要求：失败→重试成功→再次推进/下一日触发；多次失败形成正确重试链；一个 Work 的异常不能使另一个账户的 pending Work 无法入场。

### R3 · P1：成功 Run 返回 ready 后没有唤醒，持续工作静默停滞

位置：[RunLifecycleService.finish_in_uow](../../src/run_domain/lifecycle.py#L197)、[scheduler admission conditions](../../src/orchestration/work_schedule.py#L112)。

Generic brief 明确允许 `continuation.kind=ready`。但成功结束只为 `at_time/retry_after` 创建新 wakeup；admission 已经消耗/作废旧 wakeup，scheduler 又只查询存在 pending wakeup 的 Work。

复现：immediate ongoing Work → R1 succeeded，返回 `progress_saved + ready` → 运行一轮 scheduler。数据库结果为 `status=active`、`continuation=ready`、`active_coordinator_run_id=NULL`、pending wakeups=0；没有 R2。页面显示“准备继续”，系统实际上不会继续，必须由外部命令再次推进。

修复方向：可自动继续的成功结果应在结束事务内产生可去重、可审计的下一步唤醒，或使用等价的可靠 admission 机制；继续前仍检查 Work 当前状态、revision、预算和容量。不要把 awaiting_input/blocked 也自动重跑。

回归要求：真实 Generic workflow 连续两次 Run 保存/恢复进度；重复结束回执只产生一次续跑；完成、暂停、停止和新 revision 能阻止旧 continuation 入场。

### R4 · P1：Generic Work 默认预算与实际文件工具不兼容，读写文件在执行前失败

位置：[Work Run budget snapshot](../../src/run_domain/admission.py#L128)、[Work default limits](../../src/resources/work_budget.py#L19)。

存在两个必须分别修复的阻断：

1. Generic Run 只定义 model/tool_calls 限额，没有文件工具要求的 bytes_scanned、bytes_returned_to_model、bytes_written、output_file_bytes。用实际注册的 `read_file` budget metadata 预留，得到 `RunBudgetError: budget snapshot misses dimension: bytes_scanned`。
2. 新 Work 的 bytes_written/output_file_bytes 各为 100,000,000，而实际 [file write tools](../../src/sandbox/tools/local/file_write.py#L313) 每次固定预留 128 MiB（134,217,728）。完全未消耗过预算的新 Work 调用 `create_docx` 的真实预留合同，也会直接 `WorkBudgetExhausted`。实际只写很小文件也一样，因为拒绝发生在工具执行之前。

验证使用工具工厂生成的真实 metadata，调用实际 Work admission 和预算服务；不是手写一个不属于工具合同的超额请求。补齐 Run 维度后，第二个 Work 限额问题仍会存在。

修复方向：执行策略的预算策略必须覆盖实际可注册工具的维度；最大预留、实际结算和 Work 默认额度需一致。可以使用合理的有界估算，但不能以关闭预算或无限扩大额度绕过集成问题。

回归要求：全新 Generic Work 使用授权 Workspace 文件读取、分析、生成小 DOCX/XLSX，并核对 Account/Work/Run 账本；Subagent 读取路径也需覆盖。既验证预留成功，也验证超额拒绝。

### R5 · P2：Run 拒绝预算预留后，Work 层预留仍被提交并长期占用

位置：[RunBudgetService.reserve_in_uow](../../src/resources/run_budget.py#L125)、[deferred exhaustion commit](../../src/resources/run_budget.py#L94)。

当前先执行 `WorkBudgetService.mutate(... reserve)`，再检查 Run 限额。外层 `reserve()` 使用 `_defer_exhaustion=True`：Run 超额时返回错误对象，事务正常提交，最后才向调用者抛异常。于是 Work 的预留已经入账，而 Run 没有对应 ledger。

复现使用正常单次工具调用：同一 Run 完成并结算 40 次 `tool_calls=1`，第 41 次被 Run 的限额拒绝。拒绝后的 Work `reserved={tool_calls:1}`，该 operation 的 Run ledger 行数为 0；将 Run 标记 failed 后，Work 预留仍为 1。外部工具没有执行，Work 的累计可用额度却减少。

影响是反复执行到 Run 限额后积累无对应执行的持有额度，跨 Run/重试持续侵蚀 Work 预算。不能通过在 Work 完成时清空所有预留解决，这会抹掉仍有不确定外部调用的真实责任。

修复方向：Account/Work/Run 的成功预留必须一致提交；拒绝可以持久化 Run exhausted 状态，但必须回滚/补偿这次未获准的 Work reservation。覆盖 tool 路径与 model coordinator 路径的事务差异。

回归要求：第 41 次工具调用被拒绝时 Work/Run 都没有该次 held usage；合法未知结果仍保留/结算；并行分支预留不会绕过上限或重复扣账。

## 2. 五阶段的复查结论

| 阶段 | 已有证据支持的实现 | 尚不能通过的部分 |
|---|---|---|
| 1 · Work Domain Foundation (`1217d2b`) | Work、immutable requirement、CAS、Conversation 多对多关联、Work/Run 分离、单协调者和状态约束已落地；真实数据库角色测试覆盖核心约束 | 两个旧 completion 测试仍使用虚构凭据，未适应阶段四后的证据合同 |
| 2 · Execution Decoupling (`189125b`) | Work Run 不占用 Conversation active Run；执行 lease、transcript 和临时目录按 Execution 隔离；旧 revision 结果被 fence | R1 暴露出入口语义在资源层丢失；原设计要求的真实后台执行与聊天同时推进的完整入口验收仍需补齐 |
| 3 · Execution Strategy (`6a75b4c`) | 服务端注册 deterministic reminder、fixed research/artifact、generic work 策略；持久 schedule/occurrence；提醒无需模型 | R2、R3 破坏持续推进；尚不能认定每条策略的多 Run 生命周期闭环 |
| 4 · Integration (`bc33074`) | Artifact provenance、Work input/grants、累计预算、delivery receipt、Work event/SSE、模型输入关联已接入；多项隔离数据库测试通过 | R1、R4、R5；阶段报告也明确尚未完成完整入口/真实投递/故障恢复验收 |
| 5 · Optional Subagent (`0ec0763`) | 有界深度一、最多三个分支、Execution 隔离、成功兄弟复用、失败分支有限重试、required gap 阻止完成；真实 Temporal 受控分支测试通过 | 依赖的 Work budget/continuation 问题尚未解决；测试通过不等于完整真实调查与六场景验收已完成 |

本次没有发现必须将 Work 重新并回 Conversation、恢复长驻 Agent/Workflow 或重新引入 Git Session 的理由。新的职责边界总体符合冻结原则。当前问题集中在跨边界时，字段、唤醒事实和预算事务未形成一致合同。

## 3. 验证结果与证据范围

### 本轮实际运行

| 验证 | 结果 |
|---|---|
| `test/work_domain`、`test/web_persistence/test_work_integration.py`、`test/web_api/test_work_foundation.py`、`test/test_agent_segment_replay.py` | 68 passed，2 failed；含真实 PG/API+worker role、真实 Temporal 的受控执行与 replay |
| 相邻 Run/Account budget、QQ canonical ingress/delivery、schema、Research persistence、Durable/Research workflow contract 共十个测试文件 | 82 passed，6 failed |
| 前端全部现有 Vitest | 14 files、87 tests passed |
| 前端 `npm run typecheck` | 通过 |
| 五类风险的定向最小复现 | 全部复现；文件预算分别验证读/写，预算泄漏验证到 Run terminal，重试错误验证到 scheduler |

后端两组总计：150 passed，8 failed。没有运行全仓所有测试，也没有真实模型、真实 QQ 发信、浏览器全流程或 SIGKILL 全场景验收。不能将受控依赖测试描述成产品环境端到端通过。

### 八项测试失败如何解读

| 文件 / 测试 | 观察到的失败 | 复查判断 |
|---|---|---|
| `test/work_domain/test_foundation.py::test_run_success_does_not_complete_work` | 虚构 operation receipt 无法解析为持久证据 | 测试需要创建真实 receipt；不能放宽生产验收来迎合旧断言 |
| 同文件 `test_ongoing_result_never_completes_work` | 同上 | 同上；仍需保留 ongoing 不完成的行为断言 |
| `test/web_persistence/test_qq_canonical_ingress.py::test_busy_receipt_and_cancel_replays_never_target_a_later_run` | 测试全删 idempotency_commands，被新的 execution_control_events FK 拒绝 | 原“幂等缓存过期”模拟与新的命令审计引用不一致，需要明确保留合同并重构该测试；本轮未发现生产全删路径 |
| `test/test_qq_delivery_adapter.py::test_qq_parts_quote_mention_and_protected_attachment_references` | 输入仍是旧结构，缺少 route | adapter 测试未随统一 delivery 模型更新 |
| `test/web_persistence/test_research_r0_r2.py::test_trigger_task_is_atomic_idempotent_and_links_research_run` | 仍要求 hpagent-research-* workflow ID | 旧 Research 独立调度断言未更新 |
| `test/test_durable_agent_contract.py::test_canonical_registry_excludes_legacy_execution` | 注册列表未包含新增 WorkDelegationWorkflow | 阶段五改变后的 registry 合同测试未更新 |
| `test/test_research_temporal_contract.py::test_research_outbox_dispatch_uses_persisted_deterministic_workflow_id[False/True]` | 调用已删除 dispatch_research_start | 两项参数化用例仍指向旧 Research 路径，需用统一 Run dispatcher 验证相同可靠性目标 |

这八项失败不能直接等同八个产品运行缺陷；但也不能将失败测试简单删掉。原来覆盖的幂等、投递、dispatcher 和 completion 保证仍需由目标架构中的等价测试覆盖。

### 六场景的验收判断

| 原设计场景 | 本轮能确认什么 | 尚缺什么 / 已知阻断 |
|---|---|---|
| 明天九点提醒 | due occurrence、无模型执行、指定 delivery receipt 才完成的受控测试通过 | 现有部署不可用；真实入口至真实渠道的端到端验收未做 |
| 调查后台运行时继续闲聊 | 数据库 ownership/admission 与 Execution 隔离通过 | 不能用“聊天能插入 queued”替代真实执行验收；R3 会使需多步推进的工作停住 |
| QQ 创建、Web 继续 | 账户身份、跨 Conversation 关联、QQ 显式控制边界有测试 | R1；完整 QQ→Web 用户流程仍需验收 |
| 修改目标后旧分支返回 | revision/epoch fence 与 stale receipt、子分支旧结果隔离测试通过 | 需补真实运行中修改及外部副作用故障场景，不能仅依赖成功路径 |
| 三个分支之一失败 | 真实 Temporal 受控三分支、有限重试、缺口与 replay 通过 | 文件工具分支受 R4 影响；真实调查是否有上下文隔离收益尚未证明 |
| 两租户大量 Work | 聚合并发预算与 interactive capacity 预留测试通过 | R2 可中断该 worker 的公共调度任务，R5 影响累计额度；完整持续负载公平性未验证 |

## 4. 当前部署状态单列

本轮读取现有 `hpagent_web-hpagent-api-1` 日志，确认 API 因 schema 校验失败反复退出：

```text
expected_count=58 applied_count=53
missing=054_durable_work_foundation.sql,
        055_execution_decoupling.sql,
        056_execution_strategy.sql,
        057_work_integration.sql,
        058_optional_subagents.sql
Application startup failed. Exiting.
```

这是“运行环境尚未切换到五阶段目标 schema”，不是要求补历史兼容或 legacy 双轨。按照冻结原则，可以准备新的干净开发库并进行明确的环境切换；但本轮复查没有重置、迁移或删除现有业务库。隔离库可以跑到目标 schema，并不能证明现有部署已经验收可用。

阶段四 [实施记录](../implementation/durable-work-v1-phase4.md) 本身也明确：完成的是 focused verification，不是完整 real Temporal/live QQ/browser/SIGKILL acceptance campaign。

## 5. 建议关闭问题的顺序

1. 修复 R1 的群聊公开范围；同时修复 R2 的重试因果关系与调度异常隔离。
2. 修复 R3 的可靠 continuation 唤醒；补两次以上真实 Run 的持续工作验收。
3. 一并修复 R4/R5 的预算合同与事务一致性，覆盖真实文件工具和分支消费。
4. 更新八项失败测试，保留它们原本要验证的行为保证；然后重跑本轮两组测试及新增回归。
5. 在目标 schema 的运行环境完成原设计六场景与故障恢复验收，补齐阶段四/五退出记录。

不建议在这轮关闭前继续增加新的 Agent 类型、递归层数或更大的 Context 子系统。当前主架构已经足以承载这些场景，先证明现有边界能稳定履行责任。
