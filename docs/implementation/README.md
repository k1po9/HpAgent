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

- [UI-1～UI-8 重构交接](ui-refactor.md)：阶段提交、已修复的自审缺陷、完整能力矩阵、最新批次、性能/资源、059 迁移及剩余放行门槛。
- [当前前端架构](../architecture/web-ui.md)：入口与路由、状态权威、请求隔离、四桶、权限/保存、HTML 版本与旧能力迁移。
- [功能操作指南](../operations/web-workbench.md)：按 AI / 空间 / 任务使用当前产品。
- [保留验收证据](../../artifacts/product-acceptance/ui-refactor/README.md)：初始失败、完整修复、10-09 范围复验及最后完整截图；批次不累加。

原 UI 阶段计划、重复报告和自审指南已合并；历史可从阶段提交恢复。自动化通过范围与真机、读屏、真实 QQ、业务模型链及生产恢复缺口分别记录，当前仍未达到最终产品放行条件。

## 当前证据与限制

当前三入口前端及后端修复以 [UI-1～UI-8 重构交接](ui-refactor.md)与独立证据为准；交接中的 UI-8 历史批次和下列 2026-10-04 批次保留历史结论，不作为本轮通过次数。

2026-10-04 修复批次：前端 100 项、Chromium 21 项、核心后端 91 项通过；另有 Workspace API 21 项、持久化/上下文组合 67 项、Work/Run API 37 项。后端批次有重叠，不相加为不同用例总数。数据库均隔离；浏览器采用测试执行器，不能视为真实模型验收。

2026-10-04 批次曾验证 API / Worker 重载就绪、8 个 Workflow 沙箱准备、构建与静态检查；当时 MiniMax-M3 首请求连接超时，未获得 HTTP 响应，见[历史脱敏记录](../../artifacts/product-acceptance/2026-10-04/manual-e2e-provider-roundtrip.json)。2026-10-09 的当前 ModelClient 多轮合成工具 provider boundary 已通过，见[最新请求摘要](../../artifacts/product-acceptance/ui-refactor/recheck/logs/provider-roundtrip.json)；真实业务 Run Activity 链仍未完成验收。这些记录不能代替当前部署健康检查。

2026-10-03 复查记录还保留 Research 崩溃恢复限制：stage lease 默认 3600 秒，publish/save 的 Activity schedule-to-close 120 秒；SIGKILL 验收用 5 秒自然到期租约验证接管与去重，没有证明生产默认配置下快速恢复。本次协议/UI 修复未改变该策略。真实 QQ 发信、跨入口完整产品流程和持续多租户压力仍需补验收。

上述报告是带日期的证据，不是当前运行环境健康状态。复测步骤见[操作指南](../operations/web-workbench.md)和[测试指南](../development/testing.md)。
