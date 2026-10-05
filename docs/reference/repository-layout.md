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
| `scripts/check/` | 当前连接与部署检查。 |
| `scripts/benchmarks/` | 可复现的策略、Outbox 和 Temporal Recovery Benchmark。 |
| `docs/` | 当前架构、开发、运维和参考文档，以及 Durable Work / Workspace 阶段实施与验收证据。 |
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
| `web/src/components/WorkManagement.tsx` / `TestPages.tsx` | Work 创建/修订/资料授权及成果/诊断/保存页。 |
| `persistence/migrations/054–058` | Work 基础、Execution 解耦、策略、集成、有限 Subagent；实际 SQL 文件以目录为准。 |

当前文档从[总览](../architecture/overview.md)和[Work 架构](../architecture/durable-work-v1.md)进入；[实施索引](../implementation/README.md)连接历史设计、阶段报告和人工验收证据。
