# 实施与验收索引

当前架构入口：[Durable Work V1](../architecture/durable-work-v1.md)。本目录的阶段报告保留当时基线、范围与测试条件；例如 Phase 3 的“Delivery 尚未实现”和 Phase 4 的“Phase 5 禁用”是阶段历史，不是当前状态。

## Durable Work

| 记录 | 范围 |
| --- | --- |
| [目标设计](../design/durable-work-v1-implementation-design.md) | Main / Work / Run / Execution 及五阶段实施顺序；2026-10-01 研究基线。 |
| [Phase 1](durable-work-v1-phase-1-report.md) | Work / requirement / command / 资源主体基础。 |
| [Phase 2](durable-work-v1-phase-2-report.md) | Chat / Work 解耦、Execution attempt 与回执。 |
| [Phase 3](durable-work-v1-phase3.md) | 固定策略、Main 工具、持久调度、提醒与 Generic Work。 |
| [Phase 4](durable-work-v1-phase4.md) | Artifact、Work 预算、容量、统一通知/投递与 UI。 |
| [五阶段复查](../design/durable-work-v1-implementation-review-2026-10-02.md) | 包含有限 Subagent Phase 5 的源码复查及 R1–R5；未单独建立 Phase 5 报告。 |
| [复查修复](durable-work-v1-review-fixes-2026-10-03.md) | 群聊披露、重试/调度、ready 续跑、文件预算和孤立预留，受控 Temporal 与 SIGKILL 验证条件。 |
| [人工 E2E 调查](../../artifacts/product-acceptance/2026-10-04/manual-e2e-investigation.md) | 新建目录、长期文件发现、工具往返、界面操作问题与原因。 |
| [人工 E2E 实施验收](../../artifacts/product-acceptance/2026-10-04/manual-e2e-repair-results.md) | `0fcf505`：四项修复、八页面、沙箱启动校验和隔离回归。 |
| [产品验收证据入口](../../artifacts/product-acceptance/2026-10-04/README.md) | 之前的覆盖矩阵、失败记录与修复记录；不同批次各自解释。 |

## Workspace

- [Workspace P0～P5 索引](workspace-v4.1/README.md)
- [阶段总体验收](workspace-v4.1/ACCEPTANCE.md)
- [当前资源契约](../architecture/workspace-v4.1.md)：主体已统一为 Conversation / Work；Task 字样的旧报告保留历史含义。

## 前端 UI 重构

- [UI-6 自检与修复记录](ui-6-self-review.md)：R1/R2/R3 已修复；逐版本响应合并、消息行恢复轮询与当前加载默认选择，保留原 4 个失败反例及独立修复回归证据。

- [UI-6 HTML Artifact 实施报告](ui-6-html-artifact-report.md)：上下文三页签、版本与修改、幂等恢复、安全预览、下载及源码副本保存，保留 Task 原交付；自动化日志、截图和人工验收边界见报告与证据目录。

- [UI-6 HTML Artifact 闭环实施计划](ui-6-html-artifact-plan.md)：基于 `9560c99`，规划上下文 Inspector、版本与修改、幂等恢复、安全预览、源码副本保存及 Task 验收隔离；含当前代码差距、工作包和 A6-01～A6-20 验收矩阵；保留规划基线，执行结果见下方报告。

- [UI-5 自检记录](ui-5-self-review.md)：复用前一窗口反例，确认 3 个待修复 P2；本轮 80 项已有回归通过、3 项反例失败，含根因、修复建议和复验门槛。

- [UI-5 任务中心实施计划](ui-5-task-center-plan.md)：基于 `be57e0b`，规划四桶投影、完整分页、Task Inspector、创建修订、预算与投递、Research 输出和收件箱；包含 M01–M21、代码差距、分步实施与验收边界；保留规划基线，执行结果见下方报告。

- [UI-5 自检与修复记录](ui-5-self-review.md)：R1 输出缓存终态失效、R2 异常快照成果页、R3 要求重试草稿保留；3 个 P2 已修复，正式回归和本批独立证据见记录。

- [UI-5 任务中心实施报告](ui-5-task-center-report.md)：四桶与分页、Task Inspector、创建/修订、预算/投递、资料与成果、局部收件箱；命令日志、截图和真实/合成验收边界见报告及证据目录。

- [UI-4 自检记录](ui-4-self-review.md)：4 个已复现的 P2 问题、修复建议和复验门槛；本轮 78 项已有测试通过，4 项定向反例失败，状态为待修复。

- [UI-4 空间与权限实施计划](ui-4-workspace-permissions-plan.md)：基于 `40a318a`，规划空间目录与文件列表、File Inspector、共享权限编辑、上传/保存恢复、影响确认及版本 CAS；含代码差距、分步实施与验收矩阵，保留规划基线，执行结果见下方报告。

- [UI-4 自检与修复记录](ui-4-self-review.md)：R1～R4 四项缺陷证据、正式恢复/并发回归、修复后的独立验证结果；原实施日志保留为历史记录。

- [UI-4 空间与权限实施报告](ui-4-workspace-permissions-report.md)：新空间目录/文件表格、File Inspector、显式授权与失败恢复、impact/CAS；命令日志、逐项验收和未覆盖环境见报告及证据目录。

- [UI-3 执行与诊断实施计划](ui-3-execution-diagnostics-plan.md)：基于 `325a5d7` 的 UI-1/UI-2 代码，规划消息执行块、Run Inspector、审批及 Trace/Model Input 上下文化，含接口边界、实施步骤与验收矩阵；执行结果见下方报告。

- [UI-3 自检与修复记录](ui-3-self-review.md)：终态输出刷新、Model Input 权限拒绝缓存失效及轮询生命周期补验。

- [UI-3 执行与诊断实施报告](ui-3-execution-diagnostics-report.md)：消息执行块、Run 三页签、审批意图恢复及诊断隔离；自动化结果和人工/Temporal 覆盖边界见报告及证据目录。

- [UI-2 AI 主要交互实施计划](ui-2-ai-interaction-plan.md)：基于重构方案 v1.0 与 `cc40862` 的 UI-1 代码，定义 AI 对话、Composer、分页、附件与资料交互的实施及验收范围；保留规划基线，执行结果见下方报告。

- [UI-2 AI 主要交互实施报告](ui-2-ai-interaction-report.md)：实现范围、自动化覆盖、截图、命令日志与人工验收边界。

- [UI-2 自检问题修复指导](ui-2-self-review-fix-guide.md)：3 个已复现缺陷的根因、修复边界、回归用例和报告更新要求；状态为待修复。

## 当前证据与限制

2026-10-04 修复批次：前端 100 项、Chromium 21 项、核心后端 91 项通过；另有 Workspace API 21 项、持久化/上下文组合 67 项、Work/Run API 37 项。后端批次有重叠，不相加为不同用例总数。数据库均隔离；浏览器采用测试执行器，不能视为真实模型验收。

当前代码的 API / Worker 重载就绪、8 个注册 Workflow 的沙箱准备、构建与静态检查已通过。真实 MiniMax-M3 工具往返在首个请求连接阶段超时，未获得模型 HTTP 响应，见[脱敏记录](../../artifacts/product-acceptance/2026-10-04/manual-e2e-provider-roundtrip.json)。该状态不改写此前 HTTP 500 的调查，也不证明供应商往返问题已经实证消失。

2026-10-03 复查记录还保留 Research 崩溃恢复限制：stage lease 默认 3600 秒，publish/save 的 Activity schedule-to-close 120 秒；SIGKILL 验收用 5 秒自然到期租约验证接管与去重，没有证明生产默认配置下快速恢复。本次协议/UI 修复未改变该策略。真实 QQ 发信、跨入口完整产品流程和持续多租户压力仍需补验收。

上述报告是带日期的证据，不是当前运行环境健康状态。复测步骤见[操作指南](../operations/web-workbench.md)和[测试指南](../development/testing.md)。
