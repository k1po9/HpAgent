# 文档导航

当前操作以架构、开发、运维和参考文档为准。历史计划、交接与接受记录说明当时基线及验证范围，不作为当前部署健康或上线通过证明。

## 使用者

- [功能操作指南](operations/web-workbench.md)：AI、空间、任务、资料授权、成果与账户操作。
- [账户与模型治理](operations/account-governance.md)：注册、权益、额度与管理员管理。
- [HTTP API](reference/api.md)：命令、查询、错误、权限与幂等。

## 开发者

- [环境搭建](development/setup.md) → [测试分层](development/testing.md) → [脚本入口](../scripts/README.md)。
- [架构总览](architecture/overview.md)、[运行时](architecture/runtime.md)、[状态归属](architecture/data-and-state.md)、[可靠性](architecture/reliability.md)、[关键时序](architecture/sequences.md)、[能力边界](architecture/capabilities.md)。
- [Durable Work](architecture/durable-work-v1.md)、[Workspace](architecture/workspace-v4.1.md)、[Web产品与交互约束](architecture/web-ui.md)。
- [扩展方式](development/extending.md)、[配置参考](reference/configuration.md)、[目录参考](reference/repository-layout.md)、[Temporal](reference/temporal.md)。

## 运维

- [部署与升级](operations/deployment.md)：拓扑、迁移门禁、版本与生产检查。
- [运行手册](operations/runbook.md)：日常健康、启动/重载、人工操作入口。
- [日志](operations/logging.md) → [故障排查](operations/troubleshooting.md)。
- [备份与恢复](operations/backup-restore.md)：备份范围、一致性、隔离恢复和外部快照限制。

## 合同、历史与归档

当前UI约束维护在[Web架构](architecture/web-ui.md)；R1–R6接受决定、冻结指纹和当时测试范围从[接受与实施索引](implementation/README.md)进入。设计材料按[设计索引](design/README.md)区分接受依据和历史提案。完整原始材料的编号、校验和恢复方法见[归档索引](development/repository-archive-index.md)。

UI接受、自动化通过和生产上线验证分别记录，不能用旧批次通过数替代当前环境验证。
