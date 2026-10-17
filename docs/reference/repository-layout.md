# 仓库结构

| 路径 | 用途 |
| --- | --- |
| `src/` | Python Domain、Application Service、Adapter、Capability、Temporal Workflow/Activity、Worker 与 FastAPI。 |
| `web/` | React/Vite UI、Browser Test、Nginx Gateway 和前端构建配置。 |
| `config/` | 当前 Application、Model、Prompt、MCP 和 SearXNG 配置。 |
| `persistence/` | 有序 SQL Migration 与 Compose 数据库初始化。 |
| `test/` | Unit、Contract、Integration、Replay、Recovery Test 与 Fixture。 |
| `scripts/dev/` | 本地开发辅助工具。 |
| `scripts/operations/` | 部署、日志、备份、身份和可观测性工具。 |
| `scripts/check/` | 当前连接、schema、文档/CI路径及发布检查；含只读queued Run SQL。 |
| `scripts/acceptance/` | 需独立数据环境的专项验收。 |
| `scripts/benchmarks/` | 可复现的策略、Outbox 和 Temporal Recovery Benchmark。 |
| `docs/` | 当前架构、开发、运维和参考文档，以及 UI 重构 / Durable Work / Workspace 实施与验收交接。 |
| `artifacts/` | Evidence 与 Benchmark Output，不是当前文档入口。 |

`persistence/migrations/` 中的可执行 Schema 历史即使年代较早，仍是全新安装所必需的。Test Fixture 放在 `test/fixtures/`；Production `config/` 只包含运行时配置。

Workspace 代码主要位于 `src/workspace/`、`src/web_domain/file_lifecycle.py`、`src/file_runtime/` 与 `src/web_api/`；P0～P5 阶段记录位于 [`docs/implementation/workspace-v4.1/`](../implementation/workspace-v4.1/README.md)。

## Durable Work 与执行模块

| 路径 | 归属 |
| --- | --- |
| `src/conversation_domain/` | 聊天命令、admission、消息和来源投影。 |
| `src/work_domain/` | requirement、Work 命令/状态、完成证据、输入/成果/预算/目标集成。 |
| `src/run_domain/` | 通用 admission、冻结输入、Run 生命周期和结果。 |
| `src/agent_workflows/` / `src/agent_activities/` | 策略、Execution attempt / transcript / operation、有限 delegation。 |
| `src/application/work_context.py` | Work / Subagent brief，与 Main context 分开。 |
| `src/orchestration/execution_strategy.py` | 服务端注册 executor / version 决策。 |
| `src/orchestration/work_schedule.py` | PostgreSQL Work schedule / occurrence / wakeup evaluator。 |
| `src/delivery/` / `src/application/qq_delivery.py` | 统一通知/目标/投递服务与 QQ Adapter。 |
| `src/resources/` | 模型请求协议/分类、Account / Work / Run 预算和容量。 |
| `web/src/components/tasks/` / `workspace/` / `artifact/` / `run/` | Task、长期文件、HTML 和执行的当前页面/Inspector；旧 WorkManagement / TestPages 已删除。 |
| `persistence/migrations/054–059` | Work、Execution、策略、集成、有限 Subagent 与 Artifact Version API 权限收窄；实际 SQL 文件以目录为准。 |

当前文档从[总览](../architecture/overview.md)和[Work 架构](../architecture/durable-work-v1.md)进入；[实施索引](../implementation/README.md)连接历史设计、阶段报告和人工验收证据。

## Web 前端与验收

| 路径 | 归属 |
| --- | --- |
| `web/src/App.tsx` / `components/shell/` | Pre-Shell、三入口四区布局、唯一 Inspector 与 Surface。 |
| `web/src/components/conversation/` / `adapters/assistant-ui/` | 对话、Composer、消息渲染、资料入口。 |
| `web/src/store/` / `sse/` / `api/` | UI 与领域状态、账户生命周期、权威查询、Chat/Work feed 与命令。 |
| `web/src/styles/` | tokens 和按领域拆分的样式；styles.css 为统一导入入口。 |
| `web/e2e/` / `playwright.ui8-production.config.ts` | 正式浏览器回归与构建产物验收。 |
| `docs/architecture/web-ui.md` / `docs/implementation/ui-refactor.md` | 当前契约与历史交接，接受基线以R1–R6索引为准。 |
| `artifacts/product-acceptance/ui-refactor/` | 保留摘要、关键反例文字和来源/哈希；原始截图通过归档恢复。 |

阅读[前端架构](../architecture/web-ui.md)与[重构交接](../implementation/ui-refactor.md)。正式E2E的截图和trace输出在忽略的`web/test-results/`；新Benchmark原始运行在忽略的`artifacts/benchmark-runs/`。R416副本已合入`web/e2e/`，旧配置名仅选择正式源文件，见[脚本索引](../../scripts/README.md)。

## 历史产物归档

`artifacts/` 保留接受决定、合同、源码指纹、最终验证摘要、必要反例文字，以及 Benchmark 设计、报告、汇总和逐条数据。历史原始运行状态、截图、视频、trace、源码副本、本地设计包和离线镜像归档于仓库外；恢复与暂缓目录见[归档索引](../development/repository-archive-index.md)。`dist/` 仍可由离线导出脚本重新生成，不是必需源码输入。
