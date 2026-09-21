# 配置参考

配置优先级为：环境变量覆盖 `config/config.yaml` 默认值。Compose 会注入 Container 内部服务地址和数据库 DSN。Model YAML 通过 `${NAME}` 引用环境变量，Secret 不写入 YAML。

## 环境变量

| 变量 | 是否必需 | 默认值 | 所有者 | 用途 |
| --- | --- | --- | --- | --- |
| `HPAGENT_MIGRATE_PASSWORD` | 部署必需 | Compose 中为 `hpagent_migrate` | PostgreSQL | Migration Role 密码。 |
| `HPAGENT_API_PASSWORD` | 部署必需 | Compose 中为 `hpagent_api` | PostgreSQL/API | API Role 密码。 |
| `HPAGENT_WORKER_PASSWORD` | 部署必需 | Compose 中为 `hpagent_worker` | PostgreSQL/Worker | Worker Role 密码。 |
| `HPAGENT_ENV` | 否 | `development` | API/Worker | 运行环境与生产安全检查。 |
| `APP_DATABASE_URL` | 宿主机运行时 | 无 | API/Migration | Application 或 Migration PostgreSQL DSN；Compose 自动构造。 |
| `WORKER_DATABASE_URL` | Worker 必需 | 无 | Worker | Worker PostgreSQL DSN；Compose 自动构造。 |
| `REDIS_URL` | 否 | 配置或 Compose 值 | API/Worker | Redis Endpoint。 |
| `TEMPORAL_HOST` | Durable Runtime 必需 | `localhost:7233` | Worker | Temporal Frontend 地址。 |
| `TEMPORAL_TASK_QUEUE` | 否 | `hpagent-task-queue` | Worker | Scheduled Memory/默认 Queue Override。 |
| `HINDSIGHT_URL` | 否 | `http://localhost:8001` | Worker | Hindsight API 地址。 |
| `HINDSIGHT_API_LLM_*` | 启用 Hindsight LLM 时 | 无 | Hindsight | Provider、Base URL、Key 和 Model。 |
| `SILICONFLOW_API_KEY` | 取决于模型配置 | 无 | Model/Hindsight | Embedding 与 Rerank 凭据。 |
| `SILICONFLOW_BASE_URL` | 取决于模型配置 | 无 | Model/Hindsight | Provider Endpoint。 |
| `SILICONFLOW_EMBEDDING_MODEL` | 取决于模型配置 | 无 | Model/Hindsight | Embedding Model。 |
| `SILICONFLOW_RERANK_MODEL` | 取决于模型配置 | 无 | Model/Hindsight | Rerank Model。 |
| `MINIMAX_API_KEY` | 取决于模型配置 | 无 | Model | MiniMax 凭据。 |
| `MINIMAX_API_BASE_URL` | 取决于模型配置 | 无 | Model | MiniMax OpenAI-compatible Endpoint。 |
| `MINIMAX_FLAGSHIP_MODEL` | 当前 Chat Chain | 无 | Model | Fast/Chat/Reasoning Model Name。 |
| `ALIBABA_BAILIAN_*` | 取决于 Fallback | 无 | Model | Bailian Key、Endpoint 和 Fast Model。 |
| `DEEPSEEK_*` | 可选 | 见 `.env.example` | Model/Check | DeepSeek Endpoint、Key 和 Model Name。 |
| `HPAGENT_MODELS_PATH` | 否 | `/app/config/models.yaml` | Worker | 替代 Model Config 路径。 |
| `WEB_PUBLIC_ORIGIN` | 对外服务时必需 | Compose 中为 `https://localhost` | API | 允许的浏览器 Origin。 |
| `WEB_COOKIE_SECURE` | 生产必需 | `false` | API | Secure Cookie 开关。 |
| `WEB_CURSOR_SECRET` / `WEB_CURSOR_KEYS_JSON` | 部署必需 | 开发值 | API | Cursor 签名 Key 或 Key Ring。 |
| `WEB_SESSION_TOKEN_PEPPER` | 部署必需 | 开发值 | API | Session Token Pepper。 |
| `WEB_CSRF_SIGNING_KEY` | 部署必需 | 开发值 | API | CSRF 签名 Key。 |
| `QQ_BINDING_CODE_PEPPER` | 部署必需 | 开发值 | API/Worker | API 与 Worker 共享的 QQ Binding Pepper。 |
| `QQ_BINDING_CHALLENGE_SECONDS` | 否 | `300` | API/Worker | Binding Challenge TTL。 |
| `QQ_OFFICIAL_APP_ID` / `QQ_OFFICIAL_CLIENT_SECRET` | Official QQ 时 | 空 | QQ Adapter | Official Bot 凭据。 |
| `QQ_OFFICIAL_SANDBOX` | 否 | `false` | QQ Adapter | Official QQ Sandbox Endpoint 开关。 |
| `NAPCAT_ACCOUNT` / `NAPCAT_QUICK_PASSWORD` | NapCat 时 | 空 | NapCat | NapCat 登录信息。 |
| `WORKSPACE_ROOT` | 否 | `.data/workspace` | Worker | Account Workspace Root。 |
| `WORKSPACE_ISOLATION_MODE` | 否 | `single_process_account_lock` | Worker | Workspace 并发模式。 |
| `AGENT_EXECUTION_LEASE_TTL_SECONDS` | 否 | `900` | Worker | Execution Lease 时长。 |
| `WEB_FILE_UPLOAD_ENABLED` | 否 | `true` | API/Worker | Upload 能力开关。 |
| `WEB_FILE_TRANSFORM_ENABLED` | 否 | `false` | API/Worker | Transform/Output 能力开关。 |
| `WEB_FILE_SHELL_ENABLED` | 否 | `false` | API/Worker | File Shell 能力开关。 |
| `FILE_STORE_ROOT` / `FILE_RUN_ROOT` / `DOCUMENT_RUN_ROOT` | Compose 管理 | Volume 路径 | File/Document | Blob 与执行目录。 |
| `FILE_MAX_BYTES` | 否 | `134217728` | File | 单文件大小上限。 |
| `FILE_DIRECT_READ_MAX_BYTES` | 否 | `1048576` | File | Direct Read 阈值。 |
| `FILE_MAX_COUNT_PER_MESSAGE` | 否 | `10` | API | 每条消息上传数量上限。 |
| `GOTENBERG_URL` | 否 | Compose 中为 `http://gotenberg:3000` | File | 转换服务 Endpoint。 |
| `SEARXNG_URL` | 否 | Compose 中为 `http://searxng:8080` | Research | 搜索 Endpoint。 |
| `SEARXNG_SECRET` | 部署必需 | 开发值 | SearXNG | 搜索服务 Secret。 |
| `RUN_BUDGET_MODE` | 否 | `enforce` | Worker/API | Run Budget 执行模式。 |
| `LOG_LEVEL` | 否 | `INFO` | Python Service | Console Log Threshold。 |
| `LOG_DIR` | 否 | `.data/logs` | Python Service | Structured Log 目录。 |
| `WEB_GATEWAY_PORT` | 否 | `80` | Gateway | 对外 Host Port。 |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | 否 | 空/本地服务列表 | Build/Runtime | Proxy 路由。 |

`.env.example` 是部署模板。Source Settings 还提供 SSE Buffer、Outbox Recovery、Cleanup Interval 和 Budget Policy 等调优变量；没有运维证据时应保留代码默认值。

## 模型配置

`config/models.yaml` 定义 Provider，以及 `fast`、`chat`、`embedding`、`image`、`reasoning` 的有序 Model Chain 和一个 Reranker。Provider Entry 包含 API Format 和环境变量引用，凭据只保存在 `.env`。同一文件还声明 Tool Retrieval、MCP Config Path、Skill Path 和各 Surface 的 Token/Timeout Override。

每个 `ModelEntry.access_tier` 默认 `standard`；当前 `config/models.yaml` 未显式设置时采用该默认值。普通 Account 的 `account_entitlements.model_access_tier` 必须与端点 tier 相同，`owner` 可以访问所有 tier。邀请码 `--profile` 只设置新账号的 tier，不会自动创建对应模型端点。

## Prompt 配置

`config/prompts/` 包含 System Prompt、Identity、Environment、Guidance 和 Tool Summary。Prompt 变更属于运行行为变更，应配套相关测试或 Evaluation。

## MCP

`config/mcp/servers.yaml` 声明 MCP Process/Endpoint 与环境变量引用。使用 `python scripts/check/mcp-health.py` 验证语法和初始化。

## Research

`config/searxng/settings.yml` 是模板，`config/searxng/entrypoint.sh` 使用 `SEARXNG_SECRET` 和部署 Proxy 设置生成实际配置。`SEARXNG_URL` 告诉 HpAgent 查询地址。
