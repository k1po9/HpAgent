# 当前文档

首次进入从[架构总览](architecture/overview.md)和[按任务找代码](reference/repository-layout.md)开始；自动化修改规则见[根 AGENTS.md](../AGENTS.md)。

| 需要了解什么 | 入口 |
| --- | --- |
| 项目定位、职责与执行链 | [总览](architecture/overview.md)、[执行、状态与恢复](architecture/execution.md) |
| 委托、调度、完成与预算 | [Work](architecture/work.md) |
| 文件授权、版本、发布/保存、保留 | [Workspace](architecture/workspace.md) |
| 模型、工具、记忆与研究 | [能力边界](architecture/capabilities.md) |
| 前端导航、账户世代、交互与诊断 | [Web 契约](architecture/web-ui.md)、[前端指南](../web/AGENTS.md) |
| 开发环境、测试与扩展 | [搭建](development/setup.md)、[验证](development/testing.md)、[扩展](development/extending.md)、[脚本入口](../scripts/README.md) |
| API、配置与编排 | [HTTP API](reference/api.md)、[配置](reference/configuration.md)、[模型分工](reference/model-routing.md)、[Temporal](reference/temporal.md) |
| 产品操作与账号管理 | [工作台](operations/web-workbench.md)、[账户治理](operations/account-governance.md) |
| 部署与运行 | [上线配置](operations/first-release-configuration.md)、[部署](operations/deployment.md)、[运行手册](operations/runbook.md) |
| 诊断与恢复 | [日志](operations/logging.md)、[排障](operations/troubleshooting.md)、[备份恢复](operations/backup-restore.md) |
| 尚需核对的设计输入 | [设计入口](design/README.md)，不代表当前实现状态 |

文档只保留当前事实与明确限制；修改契约时原位更新。迁移和回放 fixture 是执行输入，运行报告、历史交接与验收批次不作为项目文档保存。
