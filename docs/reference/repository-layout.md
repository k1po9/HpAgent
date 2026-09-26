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
| `docs/` | 当前架构、开发、运维和参考文档，以及 Workspace 阶段实施与验收证据。 |
| `artifacts/` | Evidence 与 Benchmark Output，不是当前文档入口。 |

`persistence/migrations/` 中的可执行 Schema 历史即使年代较早，仍是全新安装所必需的。Test Fixture 放在 `test/fixtures/`；Production `config/` 只包含运行时配置。

Workspace 代码主要位于 `src/workspace/`、`src/web_domain/file_lifecycle.py`、`src/file_runtime/` 与 `src/web_api/`；P0～P5 阶段记录位于 [`docs/implementation/workspace-v4.1/`](../implementation/workspace-v4.1/README.md)。
