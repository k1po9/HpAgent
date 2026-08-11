# 架构收口整改报告

## 1. 删除内容

| File | Symbol | Reason | Replacement |
|---|---|---|---|
| `src/harness/runner.py` | `TurnOrchestrator.process_turn()` | 与统一 Agent loop 重复，QQ 已迁移 | `AgentExecutionFacade` + `DefaultBrainActionLoop` + QQ adapters |
| `src/harness/runner.py` | `archive_session()` | 归档不是 Agent loop 职责 | `SessionArchiveService.archive()` |
| `src/harness/runner.py` | `reflect()` | 反思不是 Agent loop 职责 | `MemoryReflectionService` |
| `src/harness/runner.py` | `get_metrics()` | 指标快照不是 Agent loop 职责 | `MetricsSnapshotService` |
| `orchestration.config` | `qq_execution_host_enabled` | QQ 统一 Facade 已成为唯一生产路径 | 无回退开关 |
| `orchestration.config` | `web_unified_account_enabled` | PostgreSQL 身份已成为唯一生产事实源 | 启动时强制验证 `WORKER_DATABASE_URL` |
| `orchestration.config` | 未读取的 context/summary/compress/cleanup 配置 | 删除后无生产、测试或配置读取者 | 实际上下文预算由 ContextAssembly/ContextBuilder 自身配置 |

`src/__init__.py` 已精简为包说明；架构说明迁移到本目录。

## 2. 保留的 Legacy

| 内容 | 暂时保留原因 | 当前调用者 | 后续条件 |
|---|---|---|---|
| `src/account/account_service.py` | 可能仍用于已有 JSON 数据迁移或离线检查 | 无生产 composition 调用 | 用户数据迁移确认后删除文件与 `.data/accounts.json` |
| `src/agent/` Multi-Agent 及 `ReActAgent` compatibility adapter | 有完整独立测试，但当前不是生产能力 | `test/test_agent/*`；生产启动明确拒绝 `agent.mode=multi` | 单独立项决定接入统一 execution protocol 或移除 |
| `harness` 目录名 | `activities/context_builder/prompts` 仍在使用，批量改名收益不足 | QQ Workflow、execution adapters | 后续独立命名整理 |

## 3. Agent 主链路

```text
QQ
 -> Channel / MessageIngressService / ConversationService
 -> OrchestrationWorkflow / process_turn_activity
 -> QQExecutionHost
 -> AgentExecutionFacade
 -> DefaultBrainActionLoop
 -> BrainEngine / ActionRuntime / Sandbox
 -> QQ audit + ReplyService + per-execution retain
```

```text
Web
 -> FastAPI / CommandService
 -> PostgreSQL transaction + Outbox
 -> WebOutboxDispatcher / WebRunWorkflow
 -> WebExecutionHost
 -> AgentExecutionFacade
 -> DefaultBrainActionLoop
 -> BrainEngine / ActionRuntime / Sandbox
 -> Web lifecycle terminal state + SSE + retain Outbox
```

## 4. Dependency Summary

- Surface 只依赖 application/domain/orchestration，不实现模型工具循环。
- QQ/Web Host 将各自 DTO、context、audit、reply、retain 适配到 channel-neutral Facade。
- Facade 只依赖 `BrainActionLoop` protocol；唯一生产实现是 `DefaultBrainActionLoop`。
- Loop 向下依赖 BrainEngine 与 ActionRuntime；ActionRuntime 再依赖 SandboxManager。
- PostgreSQL 是账号/身份和 Web 领域事实源；SessionStore 仅负责 QQ 短期事件/WAL。
- `bootstrap/qq.py` 组装 QQ Surface 与共享执行组件，`orchestration/worker.py` 负责运行时生命周期。

## 5. Observability Entry Points

| 边界 | 事件 |
|---|---|
| API | request/access/security events |
| Run | `run_created/completed/failed/cancelled` |
| Workflow/Outbox | `temporal_workflow_*`, `outbox_event_*` |
| Agent | `agent_execution_started/completed/failed`（QQ 与 Web） |
| Model | `model_call_started/completed/failed` |
| Tool | `tool_execution_started/completed/failed` |
| Memory | `memory_recall_*`, `memory_retain_*` |

Model/tool 事件携带独立 `execution_id`；Web 同时携带 `run_id`，QQ 不伪造 run。可用时继续携带 `workflow_id/conversation_id/session_id/account_id/surface/elapsed_ms/error_code`。

## 6. Tests

| Command | Result |
|---|---|
| `pytest` 关键 QQ/Web/context/memory/workspace 与应用服务分层用例 | 34 passed |
| `pytest test/test_application_services.py` | 3 passed（包含最终文本安全网） |
| QQ 收口回归（含新增 Host 生命周期日志后） | 8 passed |
| 变更 Python 文件 `ruff check` | passed |
| `compileall`、`AppConfig.from_yaml(config/config.yaml)` | passed |
| `git diff --check` | passed |
| `docker compose config --quiet` | passed |
| `npm run lint` | passed |
| `npm run typecheck` | passed |
| `TMPDIR=/tmp npm test` | 45 passed |
| `TMPDIR=/tmp npm run build` | passed；仅 bundle >500 kB warning |
| 完整 `pytest -m 'not postgres' test` | 观察到 190 passed；2 个真实 nsjail 执行用例因当前环境失败；300s 后终止 |
| `test_web_temporal_contract.py` | 前 10 个 passed；随后环境中的 `asyncio.to_thread` 无法调度而超时；独立 `asyncio.to_thread(lambda: 1)` 同样超时 |
| PostgreSQL/Temporal 服务 E2E 与 Playwright | 未执行：本次环境未提供可确认的外部服务状态 |

前端首次 Vitest 因继承了不存在的 Windows Temp 路径而失败；将本次命令 `TMPDIR` 指向 `/tmp` 后全部通过。

## 7. Remaining Debt

1. `account_service.py` 与 JSON 数据文件尚未物理删除，需要先确认现存用户迁移已完成；生产路径已不可达。
2. Multi-Agent 包是 experimental/inactive，仍保留通用 `process_turn` compatibility adapter，应单独决定接入统一 Facade 还是移除。
3. `worker.py` 已拆出 QQ composition，但 shared infrastructure 和运行/关闭逻辑仍较大；后续可继续拆 `bootstrap/shared.py` 与 `bootstrap/runtime.py`，不影响当前主链路唯一性。
4. 任务开始前工作区已有一批暂存的 Web 历史文档删除，以及 `docs/web/build` 的 ignored copies。本次未覆盖这些用户改动；已建立 `docs/archive/web-development/README.md`，但历史文件的最终搬迁/暂存状态仍需在提交前人工确认。
5. 前端生产 bundle 大于 500 kB；这是构建 warning，不影响本轮架构收口。
