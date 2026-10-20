# 项目工作指南

HpAgent 是 Web / QQ 共用的 AI 助手平台：Main 面向交互，Work 保存持续委托，Run 是有限执行，Execution 隔离执行上下文。先确定修改所属边界，再沿命令、事务、编排、查询和 UI 修改。

## 阅读路径

- 首次进入：[架构总览](docs/architecture/overview.md) → [代码地图](docs/reference/repository-layout.md)。不要全量读取 docs。
- 命令、执行、恢复：[执行与状态](docs/architecture/execution.md)。
- 委托、调度、完成、预算：[Work](docs/architecture/work.md)。
- 文件、权限、版本、保存、GC：[Workspace](docs/architecture/workspace.md)。
- 前端：先读 [web/AGENTS.md](web/AGENTS.md)，再按需读 [Web 契约](docs/architecture/web-ui.md)。
- 模型/工具/记忆：[能力边界](docs/architecture/capabilities.md)；配置与 API 从 [文档导航](docs/README.md)定位。
- 环境和验证：[开发环境](docs/development/setup.md)、[测试指南](docs/development/testing.md)、[脚本入口](scripts/README.md)。

## 修改时保持的边界

- PostgreSQL 是业务状态权威；Redis/SSE 负责通知或缓存。状态与 Outbox 同事务提交，不能用 Workflow completed、UI 动效或缓存推断业务完成。
- 聊天保持同 Conversation 单 active chat Run；Work Run 不创建聊天 Message、不占聊天槽。关联 Conversation 不导入聊天全文或文件权限。
- Work 协调权、Execution attempt lease/fence、资源锁、跨进程容量分别校验。旧 attempt、旧 revision、迟到响应不得推进新状态。
- Workflow 仅编排；数据库、模型、网络、文件、工具 I/O 放在 Activity/Adapter。注意间接导入也会进入 Temporal sandbox。
- 命令响应不确定时保留原幂等键和 payload，查询权威事实后恢复；外部副作用未知时保留 uncertain 和显式决议。
- 模型请求先规范化校验，再冻结 Snapshot，再原子预留 Account/day、Work（若有）、Run 预算。快照不证明请求已发出。
- Run succeeded、Work completed、成果发布、长期保存、版本采用、渠道 accepted、用户验收是独立事实。
- Run 冻结文件候选/版本不等于永久授权；使用和提交仍检查当前 grant、revision、fence。普通文件不依赖 Git，Git 只服务代码任务。
- 文件入口与不可变字节分开；版本更新保留 CAS；删除入口不绕过引用检查和 GC。
- 保持账号过滤、组合外键、最小数据库角色权限；不输出 .env、完整容器环境、凭据或模型输入正文。
- 当前模块有跨领域依赖；先跟踪实际调用，不假设目录已经实现严格分层。业务改动同步命令、查询、前端与相关契约。

## 验证选择

从仓库根执行；公共命令见 Makefile。运行前检查所需依赖，缺依赖要明确报告。

| 修改 | 入口 |
| --- | --- |
| 文档/导航/公共命令 | `make docs-check scripts-test` |
| Python 规则/编排 | `make lint typecheck test-unit`，按改动选相关用例 |
| 持久化/权限/Work | `make test-db` |
| HTTP/查询/SSE | `make test-api` |
| Workflow/Activity/恢复 | `make test-temporal`，含注册与 sandbox 约束 |
| 前端 | `make ci-web`；交互改动按测试指南选浏览器用例 |

PG fixture 可能清表；浏览器会迁移并写账户/文件；Temporal 有 SIGKILL harness。必须使用显式专用 DSN、Redis、File Store、namespace 和端口，不借用现有业务服务。迁移只使用原有完整链与 checksum，不修改已应用 SQL。

真实 Provider、QQ、发布 smoke、benchmark 有外部请求或部署副作用，按任务授权范围执行。不要为文档或局部修复重启现有部署。结果区分静态、单元、隔离集成、浏览器与真实链路；未执行不能写成通过。

## 文档维护

仅维护当前事实、约束、操作和已知限制；修改契约时同步对应文档。优先修改现有页面，不新增阶段报告、交接记录、历史索引或重复方案。设计目录只存明确尚需讨论的输入，不能覆盖已实现事实。运行输出放忽略目录或仓库外，不提交日志、截图、trace、历史验收及 benchmark 结果。迁移链与测试回放 fixture 属于可执行输入，必须保留。
