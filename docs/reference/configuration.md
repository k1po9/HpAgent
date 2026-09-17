# Configuration Reference

Precedence is environment variables over `config/config.yaml` defaults. Compose supplies container URLs and database DSNs. Model YAML references environment variables with `${NAME}` placeholders; secrets are never committed to YAML.

## Environment variables

| Variable | Required | Default | Owner | Purpose |
| --- | --- | --- | --- | --- |
| `HPAGENT_MIGRATE_PASSWORD` | deployment | `hpagent_migrate` in Compose | PostgreSQL | Migration-role password. |
| `HPAGENT_API_PASSWORD` | deployment | `hpagent_api` in Compose | PostgreSQL/API | API-role password. |
| `HPAGENT_WORKER_PASSWORD` | deployment | `hpagent_worker` in Compose | PostgreSQL/worker | Worker-role password. |
| `HPAGENT_ENV` | no | `development` | API/worker | Runtime environment and production safety checks. |
| `APP_DATABASE_URL` | host runtime | none | API/migrations | Application or migration PostgreSQL DSN; Compose constructs it. |
| `WORKER_DATABASE_URL` | yes for worker | none | worker | Worker PostgreSQL DSN; Compose constructs it. |
| `REDIS_URL` | no | config/Compose value | API/worker | Redis endpoint. |
| `TEMPORAL_HOST` | yes for durable runtime | `localhost:7233` | workers | Temporal frontend address. |
| `TEMPORAL_TASK_QUEUE` | no | `hpagent-task-queue` | worker | Scheduled-memory/default queue override. |
| `HINDSIGHT_URL` | no | `http://localhost:8001` | worker | Hindsight API endpoint. |
| `HINDSIGHT_API_LLM_*` | yes when Hindsight LLM is enabled | none | Hindsight | Provider, base URL, key, and model. |
| `SILICONFLOW_API_KEY` | model-dependent | none | models/Hindsight | Embedding and rerank credential. |
| `SILICONFLOW_BASE_URL` | model-dependent | none | models/Hindsight | Provider endpoint. |
| `SILICONFLOW_EMBEDDING_MODEL` | model-dependent | none | models/Hindsight | Embedding model. |
| `SILICONFLOW_RERANK_MODEL` | model-dependent | none | models/Hindsight | Rerank model. |
| `MINIMAX_API_KEY` | model-dependent | none | models | MiniMax credential. |
| `MINIMAX_API_BASE_URL` | model-dependent | none | models | MiniMax OpenAI-compatible endpoint. |
| `MINIMAX_FLAGSHIP_MODEL` | current chat chain | none | models | Fast/chat/reasoning model name. |
| `ALIBABA_BAILIAN_*` | fallback-dependent | none | models | Bailian key, endpoint, and fast model. |
| `DEEPSEEK_*` | optional | documented in `.env.example` | models/checks | DeepSeek endpoint, key, and model names. |
| `HPAGENT_MODELS_PATH` | no | `/app/config/models.yaml` | worker | Alternate model configuration path. |
| `WEB_PUBLIC_ORIGIN` | yes when exposed | `https://localhost` in Compose | API | Allowed browser origin. |
| `WEB_COOKIE_SECURE` | production | `false` | API | Secure-cookie enforcement. |
| `WEB_CURSOR_SECRET` / `WEB_CURSOR_KEYS_JSON` | deployment | development value | API | Cursor signing key or key ring. |
| `WEB_SESSION_TOKEN_PEPPER` | deployment | development value | API | Session token pepper. |
| `WEB_CSRF_SIGNING_KEY` | deployment | development value | API | CSRF signing key. |
| `QQ_BINDING_CODE_PEPPER` | deployment | development value | API/worker | Shared QQ binding-code pepper. |
| `QQ_BINDING_CHALLENGE_SECONDS` | no | `300` | API/worker | Binding challenge TTL. |
| `QQ_OFFICIAL_APP_ID` / `QQ_OFFICIAL_CLIENT_SECRET` | official QQ only | empty | QQ adapter | Official bot credentials. |
| `QQ_OFFICIAL_SANDBOX` | no | `false` | QQ adapter | Official QQ sandbox endpoint selection. |
| `NAPCAT_ACCOUNT` / `NAPCAT_QUICK_PASSWORD` | NapCat only | empty | NapCat | NapCat login. |
| `WORKSPACE_ROOT` | no | `.data/workspace` | worker | Account workspace root. |
| `WORKSPACE_ISOLATION_MODE` | no | `single_process_account_lock` | worker | Workspace concurrency mode. |
| `AGENT_EXECUTION_LEASE_TTL_SECONDS` | no | `900` | worker | Execution lease duration. |
| `WEB_FILE_UPLOAD_ENABLED` | no | `true` | API/worker | Upload capability flag. |
| `WEB_FILE_TRANSFORM_ENABLED` | no | `false` | API/worker | Transform/output capability flag. |
| `WEB_FILE_SHELL_ENABLED` | no | `false` | API/worker | File shell capability flag. |
| `FILE_STORE_ROOT` / `FILE_RUN_ROOT` / `DOCUMENT_RUN_ROOT` | Compose-managed | volume paths | file/document | Blob and execution directories. |
| `FILE_MAX_BYTES` | no | `134217728` | file | Per-file byte limit. |
| `FILE_DIRECT_READ_MAX_BYTES` | no | `1048576` | file | Direct-read threshold. |
| `FILE_MAX_COUNT_PER_MESSAGE` | no | `10` | API | Upload count limit. |
| `GOTENBERG_URL` | no | `http://gotenberg:3000` in Compose | file | Conversion service endpoint. |
| `SEARXNG_URL` | no | `http://searxng:8080` in Compose | research | Search endpoint. |
| `SEARXNG_SECRET` | deployment | development value | SearXNG | Search service secret. |
| `RUN_BUDGET_MODE` | no | `enforce` | worker/API | Run budget enforcement mode. |
| `LOG_LEVEL` | no | `INFO` | all Python services | Console log threshold. |
| `LOG_DIR` | no | `.data/logs` | Python services | Structured log directory. |
| `WEB_GATEWAY_PORT` | no | `80` | gateway | Public host port. |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | no | empty/local service list | builds/runtime | Proxy routing. |

`.env.example` is the deployment template. Source settings may expose additional tuning variables for SSE buffers, Outbox recovery, cleanup intervals, and budget policy; leave them at source defaults unless operating evidence calls for a change.

## Model configuration

`config/models.yaml` defines providers and ordered chains for `fast`, `chat`, `embedding`, `image`, and `reasoning`, plus one reranker. Provider entries contain endpoint format and environment references; credentials stay in `.env`. Tool retrieval, MCP configuration path, skill path, and surface-specific token/timeout overrides are declared in the same file.

## Prompt configuration

`config/prompts/` contains the system prompt, identities, environment description, guidance, and tool summary. Treat prompt changes as runtime behavior changes and cover them with relevant tests or evaluation.

## MCP

`config/mcp/servers.yaml` declares MCP processes/endpoints and environment references. Validate syntax and initialization with `python scripts/check/mcp-health.py`.

## Research

`config/searxng/settings.yml` is a template rendered by `config/searxng/entrypoint.sh` using `SEARXNG_SECRET` and deployment proxy settings. `SEARXNG_URL` tells HpAgent where to query it.
