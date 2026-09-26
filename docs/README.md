# HpAgent 文档

架构、开发、运维和参考文档描述当前系统；`implementation/` 保存 Workspace v4.1 的阶段证据与验收边界。

## 架构

- [架构总览](architecture/overview.md)：系统上下文、逻辑分层、入口和标准执行流。
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

- [Workspace P0～P5 记录与总体验收](implementation/workspace-v4.1/README.md)
