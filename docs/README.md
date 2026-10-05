# HpAgent 文档

架构、开发、运维和参考文档描述当前实现（同步日期：2026-10-05，代码基线 `0fcf505`）。`design/` 保存设计和复查基线；`implementation/` 保存阶段证据，不应把阶段末的“尚未实现”当作当前能力状态。

推荐阅读：架构总览 → Durable Work V1 → 功能操作指南 → API / 测试 / 运维；历史设计与产品验收从实施索引进入。

## 架构

- [架构总览](architecture/overview.md)：系统上下文、逻辑分层、入口和标准执行流。
- [Durable Work V1](architecture/durable-work-v1.md)：Main / Work / Run / Execution、策略、调度、交付、预算和有限分支。
- [运行时](architecture/runtime.md)：命令接收、分发、Workflow、策略和 Activity。
- [数据与状态](architecture/data-and-state.md)：状态归属和持久化边界。
- [能力边界](architecture/capabilities.md)：Agent、工具、记忆、文件、研究、Artifact 和文档。
- [可靠性](architecture/reliability.md)：持久化、重试、Lease、Fencing、幂等与取消。
- [关键时序](architecture/sequences.md)：核心端到端流程。
- [Workspace v4.1](architecture/workspace-v4.1.md)：长期文件目录、授权、Run 快照、版本、保存与 GC。

## 开发

- [环境搭建](development/setup.md)
- [测试](development/testing.md)
- [扩展 HpAgent](development/extending.md)

## 运维

- [功能操作指南](operations/web-workbench.md)：八个页面、文件授权、持续工作、成果保存与人工复测。
- [Account 与模型治理](operations/account-governance.md)
- [部署](operations/deployment.md)
- [运行手册](operations/runbook.md)
- [日志](operations/logging.md)
- [故障排查](operations/troubleshooting.md)
- [备份与恢复](operations/backup-restore.md)

## 参考

- [配置](reference/configuration.md)
- [仓库结构](reference/repository-layout.md)
- [Temporal](reference/temporal.md)
- [HTTP API](reference/api.md)

## 实施与验收

- [实施与验收索引](implementation/README.md)：五阶段、复查修复、人工 E2E 与剩余运行验收。
- [Workspace P0～P5 记录与总体验收](implementation/workspace-v4.1/README.md)
