# 历史实施与接受依据

当前能力和操作分别从[架构总览](../architecture/overview.md)、[功能指南](../operations/web-workbench.md)、[部署](../operations/deployment.md)和[测试指南](../development/testing.md)进入。本目录记录当时基线、变更、验证范围和限制；阶段“未实现”不表示当前仍未实现，旧通过数不表示当前部署已验证。

## 接受基线

- [R1–R6接受索引](../design/ui-iteration-2026-10/README.md)：各轮最终决定、合同、冻结指纹与R6收尾。当前约束维护在[Web架构](../architecture/web-ui.md)。
- [UI-1～UI-8历史交接](ui-refactor.md)：更早的重构、缺陷和059权限迁移依据。
- [Workspace阶段索引](workspace-v4.1/README.md)及[当时验收](workspace-v4.1/ACCEPTANCE.md)；当前主体与权限以[Workspace架构](../architecture/workspace-v4.1.md)为准。

## Durable Work 历史

[Phase 1](durable-work-v1-phase-1-report.md)、[Phase 2](durable-work-v1-phase-2-report.md)、[Phase 3](durable-work-v1-phase3.md)、[Phase 4](durable-work-v1-phase4.md)、[当时复查](../design/durable-work-v1-implementation-review-2026-10-02.md)、[复查修复](durable-work-v1-review-fixes-2026-10-03.md)保留各阶段实现和测试条件；当前运行时、Execution、投递、预算及有限delegation见[Work架构](../architecture/durable-work-v1.md)。

## 验证边界与恢复

历史浏览器Fake、受控Provider协议、短租约SIGKILL和真实模型业务链分别记账，不累加重叠集合。真实供应商、生产Worker、QQ外发、真机/读屏、长期压力与匹配快照恢复仍须按发布范围形成当前证据。Research默认lease与历史短租约实验的差异见[可靠性](../architecture/reliability.md)。

完整原材料、退役Prompt与工具通过[归档索引](../development/repository-archive-index.md)恢复。认证原型的独立Flip/CPU/释放断言尚未全部证明由产品覆盖，暂时保留；详见[历史认证交接](auth-entry-transition-2026-10-10.md)，它不是当前启动指南。
