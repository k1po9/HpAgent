# 配置参考

配置由 `config/config.yaml`、`config/models.yaml`、`config/prompts/` 和环境变量共同组成。密钥只通过环境变量注入；参考仓库根目录 `.env.example`，不要提交真实 `.env`。

## 必需运行配置

| 变量 | 消费者 | 说明 |
|---|---|---|
| `WORKER_DATABASE_URL` | `hpagent` | PostgreSQL worker DSN；缺失时 Worker fail closed |
| `APP_DATABASE_URL` | API | PostgreSQL API DSN |
| `TEMPORAL_HOST` | Worker | Temporal gRPC 地址 |
| `REDIS_URL` | Worker/API | QQ 热状态和 Web 在线事件；不可用时部分能力降级 |
| `HINDSIGHT_URL` | Worker | 长期记忆 API |
| `WORKSPACE_ROOT` | Worker | 账号 workspace 根目录 |
| `WORKSPACE_ISOLATION_MODE` | Worker | 当前生产模式为 `single_process_account_lock` |

## Web

`WEB_PUBLIC_ORIGIN`、cursor signing、session pepper、CSRF key 和 `QQ_BINDING_CODE_PEPPER` 属于安全配置，生产环境不得使用 development 默认值。API 与 QQ Worker 必须使用相同的 QQ binding pepper；`QQ_BINDING_CHALLENGE_SECONDS` 默认 300。Web password 的运行时真相源是 PostgreSQL `web_credentials`。Canonical Agent worker 总是注册；Outbox lease/recovery 参数必须为正且 recovery interval 小于 lease timeout。

## Durable Agent

| 变量 | 默认值 | 消费者 | 说明 |
|---|---:|---|---|
| `AGENT_EXECUTION_LEASE_TTL_SECONDS` | `900` | Worker | PostgreSQL account execution lease TTL；必须为正，并大于最长单次 Activity 超时且留出恢复余量 |

启动前应用全部 migrations（包括执行分段与等待的 `033_agent_execution_segments.sql`）。
Web 固定使用 canonical durable lifecycle，ReAct 与 Plan-and-Execute 均可选；没有
legacy/durable 分流开关。lease TTL 只限制执行分段，不限制 Run 总寿命。

## 模型与工具

模型 provider、fallback chain、timeout 和 token 上限在 `config/models.yaml` 定义；API key 由同名环境变量替换。MCP、Skills、Tool RAG 和 native tools 由配置文件开启，启动失败按模块记录降级日志。

## 已移除迁移开关

`QQ_EXECUTION_HOST_ENABLED` 和 `WEB_UNIFIED_ACCOUNT_ENABLED` 已移除。QQ 始终使用统一 Agent Facade，QQ/Web 身份始终使用 PostgreSQL。
