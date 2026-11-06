# 当前文档

首次进入从[架构总览](architecture/overview.md)和[按任务找代码](reference/repository-layout.md)开始；自动化修改规则见[根 AGENTS.md](../AGENTS.md)。

| 需要了解什么 | 入口 |
| --- | --- |
| 项目定位、职责与执行链 | [总览](architecture/overview.md)、[执行、状态与恢复](architecture/execution.md) |
| 命令接收、事务与持久化 | [入口调用图](architecture/execution.md#业务主干调用图)、[Chat 时序](architecture/execution.md#chat-时序与事务)、[Work 时序](architecture/execution.md#work-时序与事务)、[UoW 所有权](architecture/execution.md#unitofwork-所有权) |
| 应用入口、兼容路径与 SQL writer | [代码地图](reference/repository-layout.md)、[核心仓储与表写入责任](reference/repository-layout.md#核心仓储职责与表写入责任)、[阅读顺序](reference/repository-layout.md#从简单到复杂阅读业务代码) |
| 委托、调度、完成与预算 | [Work](architecture/work.md) |
| 文件授权、版本、发布/保存、保留 | [Workspace](architecture/workspace.md) |
| 模型、工具、记忆与研究 | [能力边界](architecture/capabilities.md) |
| 前端导航、账户世代、交互与诊断 | [Web 契约](architecture/web-ui.md)、[前端指南](../web/AGENTS.md) |
| 独立环境中的真实浏览器闭环 | [浏览器验收步骤](development/testing.md#真实浏览器全链路)、[检索配置与 .env](reference/configuration.md#web-retriever-与-research) |
| 开发环境、测试与扩展 | [搭建](development/setup.md)、[验证](development/testing.md)、[扩展](development/extending.md)、[脚本入口](../scripts/README.md) |
| API、配置与编排 | [HTTP API](reference/api.md)、[配置](reference/configuration.md)、[模型分工](reference/model-routing.md)、[Temporal](reference/temporal.md) |
| 产品操作与账号管理 | [工作台](operations/web-workbench.md)、[账户治理](operations/account-governance.md) |
| 部署与运行 | [上线配置](operations/first-release-configuration.md)、[部署](operations/deployment.md)、[运行手册](operations/runbook.md) |
| 诊断与恢复 | [日志](operations/logging.md)、[排障](operations/troubleshooting.md)、[备份恢复](operations/backup-restore.md) |
| 尚需核对的设计输入 | [设计入口](design/README.md)，不代表当前实现状态 |

文档只保留当前事实与明确限制；修改契约时原位更新。迁移和回放 fixture 是执行输入，运行报告、历史交接与验收批次不作为项目文档保存。

修改业务命令时，配套阅读[扩展规则](development/extending.md#添加应用能力)与[按业务路径选测](development/testing.md#业务命令与持久化选测)；运行输出保存在仓库外或忽略目录，不将一次性 runner、审计脚本和阶段日志作为维护入口。
