# 配置参考

## Agent 工具分配

[config/agent_tools.yaml](../../config/agent_tools.yaml) 是实际执行的工具分配配置；[config/prompts/agents.yaml](../../config/prompts/agents.yaml) 保存对应中文角色与阶段提示词。两者均按仓库 `src/` 或主 Worker 平铺目录布局定位应用根目录，进程首次读取后缓存；修改后需重启相关 Worker，不热更新已有模型快照和上下文。

- `groups` 定义工具用途、准确 `tools` 名称或动态 `category`（`mcp` / `skill`），每个工具或动态类别只属于一个组。所有原生工具须有归属；拼写错误、未知角色/工具/组、重复 YAML 键及不支持的授予方式直接报错，不回退到宽松策略。
- `roles.<角色>.groups` 决定该角色允许的工具组。关闭某角色的研究能力，可从该角色列表移除 `research`，仍保留组定义供其他角色使用；关闭代码写入同理移除 `code_write`，Shell 需另外移除 `shell`。分配不会创建实现或跳过服务、渠道、文件范围、预算与审批检查。
- `exposure=existing` 保留工具原有常驻、文件入口或按需检索规则；`always` 改为满足运行条件后常驻；`semantic` 改为按需检索。检索总上限仍生效，常驻/入口工具过多时报配置错误。复杂能力、联网、结果管理和委派属于根执行追加工具，只支持 `always`，通过移除角色的组来关闭。
- Main 与 Worker 的 `grant=configured` 从配置组取工具，但不能越过服务端角色边界：Main 不获得后台分支委派或 Work 专用文字发布，Worker 不获得 Work 管理；只读子 agent 使用 `parent_read_only_subset`，不能改成自行授予全部工具。父侧填写的分支工具仍须通过服务器核验，文件/目录引用不等于授权。
- Research 合成、HTML 生成、规划、评估、工具摘要和记忆查询改写模型固定为 `grant=none`、空工具组；它们使用固定服务端流程，不因配置存在而变成工具循环 agent。MCP/Skill 工具不能以原生或专用工具的同名名称跨类别取得分配。

配置减少权限后，新的工具使用按当前分配检查；已提交的工具回执不会重新执行，已登记的复杂能力实例按原不可变契约恢复，不因配置变化重新发送模型请求。修改提示词时对照组用途和角色列表；真正发给模型的本轮工具清单还受服务和检索限制，不能把允许列表理解成始终可用。

首次上线的取值决定集中在[配置确认表](../operations/first-release-configuration.md)，Compose 可插值的全部变量集中在[.env.example](../../.env.example)。本页维护各配置入口的语义。

配置优先级为：环境变量覆盖 `config/config.yaml` 默认值。Compose 会注入 Container 内部服务地址和数据库 DSN。Model YAML 通过 `${NAME}` 引用环境变量，Secret 不写入 YAML。

## 环境变量

| 变量 | 是否必需 | 默认值 | 所有者 | 用途 |
| --- | --- | --- | --- | --- |
| `HPAGENT_MIGRATE_PASSWORD` | 部署必需 | 无，Compose 启动前必须设置 | PostgreSQL | Migration Role 密码。 |
| `HPAGENT_API_PASSWORD` | 部署必需 | 无，Compose 启动前必须设置 | PostgreSQL/API | API Role 密码。 |
| `HPAGENT_WORKER_PASSWORD` | 部署必需 | 无，Compose 启动前必须设置 | PostgreSQL/Worker | Worker Role 密码。 |
| `HPAGENT_ENV` | 否 | `development` | API/Worker | 运行环境与生产安全检查。 |
| `APP_DATABASE_URL` | 宿主机运行时 | 无 | API/Migration | Application 或 Migration PostgreSQL DSN；Compose 自动构造。 |
| `WORKER_DATABASE_URL` | Worker 必需 | 无 | Worker | Worker PostgreSQL DSN；Compose 自动构造。 |
| `REDIS_URL` | 否 | 配置或 Compose 值 | API/Worker | Redis Endpoint。 |
| `TEMPORAL_HOST` | Durable Runtime 必需 | `localhost:7233` | Worker | Temporal Frontend 地址。 |
| `TEMPORAL_TASK_QUEUE` | 否 | `hpagent-task-queue` | Worker | Scheduled Memory/默认 Queue Override。 |
| `HINDSIGHT_URL` | 否 | Worker 配置；API 环境默认 `http://hindsight:8888` | API/Worker | 同一 Hindsight 目标；默认值及环境覆盖须与部署一致，详见下方治理连接。 |
| `HINDSIGHT_API_LLM_*` | 启用 Hindsight LLM 时 | 无 | Hindsight | Provider、Base URL、Key 和 Model。 |
| `SILICONFLOW_API_KEY` | 取决于模型配置 | 无 | Model/Hindsight | Embedding 与 Rerank 凭据。 |
| `SILICONFLOW_BASE_URL` | 取决于模型配置 | 无 | Model/Hindsight | Provider Endpoint。 |
| `SILICONFLOW_EMBEDDING_MODEL` | 取决于模型配置 | 无 | Model/Hindsight | Embedding Model。 |
| `SILICONFLOW_RERANK_MODEL` | 取决于模型配置 | 无 | Model/Hindsight | Rerank Model。 |
| `MINIMAX_API_KEY` | 取决于模型配置 | 无 | Model | MiniMax 凭据。 |
| `MINIMAX_API_BASE_URL` | 取决于模型配置 | 无 | Model | MiniMax OpenAI-compatible Endpoint。 |
| `MINIMAX_FLAGSHIP_MODEL` / `MINIMAX_STANDARD_MODEL` / `MINIMAX_HIGHSPEED_MODEL` | 取决于启用链 | 无 | Model | MiniMax模型目录；当前Worker未选用，Hindsight默认引用STANDARD；详见模型分工表。 |
| `SILICONFLOW_FLAGSHIP_MODEL` / `SILICONFLOW_STANDARD_MODEL` / `SILICONFLOW_HIGHSPEED_MODEL` | 可选 | 空 | Model | 当前分别用于reasoning / chat / fast，不自动回退。 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_API_BASE_URL` / `DEEPSEEK_*_MODEL` | 可选 | 见 `.env.example` | Model/Check | DeepSeek 模型 Endpoint、Key 和 Model Name，与检索凭据独立。 |
| `WEB_RETRIEVAL_SEARCH_PROVIDER` | 否 | `legacy` | Worker | Main / Work 共享搜索供应商：legacy（SearXNG 等）或 deepseek；选择 deepseek 时必须配置检索 Key。 |
| `WEB_RETRIEVAL_FETCH_PROVIDER` | 否 | `local` | Worker | 正文供应商：local 或 deepseek；本地默认 Text，云端 Markdown 的最终 URL 不可验证。 |
| `DEEPSEEK_API_SEARCH_FETCH` | 使用 DeepSeek 检索时必需 | 空 | Worker | Search/Fetch 专用凭据；不是模型 DEEPSEEK_API_KEY，不写入源码、日志或模型输入。 |
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
| `WORKSPACE_ROOT` | 否 | `.data/workspace` | Worker | Git 代码工作区根；不是长期文件目录。 |
| `WORKSPACE_ISOLATION_MODE` | 否 | `single_process_account_lock` | Worker | 可选 Git 工作区并发模式；通用 Execution scratch 不依赖 Account Git 锁。 |
| `AGENT_EXECUTION_LEASE_TTL_SECONDS` | 否 | `900` | Worker | 主执行路径的 Execution attempt Lease 配置；Research stage 默认租约另见恢复限制。 |
| `WEB_FILE_UPLOAD_ENABLED` | 否 | `true` | API/Worker | Upload 与不可变成果 publisher 开关；Research/HTML/普通 Markdown 交付都需要 publisher。 |
| `WEB_FILE_TRANSFORM_ENABLED` | 否 | `false` | API/Worker | 文件格式转换开关；普通 Markdown 发布不要求开启转换。 |
| `WEB_FILE_SHELL_ENABLED` | 否 | `false` | API/Worker | File Shell 能力开关。 |
| `FILE_STORE_ROOT` / `FILE_RUN_ROOT` / `DOCUMENT_RUN_ROOT` | Compose 管理 | Volume 路径 | File/Document | 长期文件字节与隔离执行目录；目录/授权/修订在 PostgreSQL。 |
| `FILE_MAX_BYTES` | 否 | `134217728` | File | 单文件大小上限。 |
| `FILE_DIRECT_READ_MAX_BYTES` | 否 | `1048576` | File | Direct Read 阈值。 |
| `FILE_MAX_COUNT_PER_MESSAGE` | 否 | `10` | API | 每条消息上传数量上限。 |
| `GOTENBERG_URL` | 否 | Compose 中为 `http://gotenberg:3000` | File | 转换服务 Endpoint。 |
| `SEARXNG_URL` | 否 | Compose 中为 `http://searxng:8080` | Research | 搜索 Endpoint。 |
| `SEARXNG_SECRET` | 部署必需 | 开发值 | SearXNG | 搜索服务 Secret。 |
| `ARTIFACT_MODEL_READ_TIMEOUT_SECONDS` | 否 | `90` | Worker | HTML 成果生成独立读取预算，合法范围 5–300 秒；连接预算最多 5 秒。 |
| `HPAGENT_MIGRATIONS_DIR` | 宿主机/CI 建议显式设置 | 部署挂载路径 | API/Worker/Migrate/Test | 指向完整 `persistence/migrations`；Compose 使用 `/opt/hpagent/migrations`，CI 使用仓库绝对路径。 |
| `RUN_BUDGET_MODE` | 否 | `enforce` | Worker/API | Run Budget 执行模式。 |
| `LOG_LEVEL` | 否 | `INFO` | Python Service | Console Log Threshold。 |
| `LOG_DIR` | 否 | `.data/logs` | Python Service | Structured Log 目录。 |
| `WEB_GATEWAY_PORT` | 否 | `80` | Gateway | 对外 Host Port。 |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | 否 | 空/本地服务列表 | Build/Runtime | Proxy 路由。 |

`.env.example` 是部署模板。Source Settings 还提供 SSE Buffer、Outbox Recovery、Cleanup Interval 和 Budget Policy 等调优变量；没有运维证据时应保留代码默认值。

## 模型配置

完整的用途、供应商、变量、调用位置和脱敏检查见[模型分工表](model-routing.md)。Research报告与HTML成果共用chat，长期记忆是独立的Hindsight配置，reasoning不自动被业务选用。

`config/models.yaml` 定义 Provider，以及 `fast`、`chat`、`embedding`、`image`、`reasoning` 的有序 Model Chain 和一个 Reranker。Provider Entry 包含 API Format 和环境变量引用，凭据只保存在 `.env`。同一文件还声明 Tool Retrieval、MCP Config Path、Skill Path 和各 Surface 的 Token/Timeout Override。

每个 `ModelEntry.access_tier` 默认 `standard`；当前 `config/models.yaml` 未显式设置时采用该默认值。普通 Account 的 `account_entitlements.model_access_tier` 必须与端点 tier 相同，`owner` 可以访问所有 tier。邀请码 `--profile` 只设置新账号的 tier，不会自动创建对应模型端点。

## Prompt 配置

`config/prompts/` 包含 System Prompt、Identity、Environment、Guidance 和 Tool Summary。Prompt 变更属于运行行为变更，应配套相关测试或 Evaluation。

## MCP

`config/mcp/servers.yaml` 声明 MCP Process/Endpoint 与环境变量引用。使用 `python scripts/check/mcp-health.py` 验证语法和初始化。

## Web Retriever 与 Research

在独立目录的私有 `.env` 中显式选择已验证的后端组合：

```dotenv
WEB_RETRIEVAL_SEARCH_PROVIDER=deepseek
WEB_RETRIEVAL_FETCH_PROVIDER=local
DEEPSEEK_API_SEARCH_FETCH=填写检索专用密钥
```

检索 Key 独立于模型 Provider 配置。Compose 已将这三个变量传给主 Worker；API 不执行检索。旧 `.env` 不会因更新 `.env.example` 自动补齐，默认仍为 legacy/local。只修改私有配置时保留旧值和其他凭据，配置完成后在目标目录 recreate 对应 Worker；单纯 restart 不加载新环境。宿主进程需由启动入口显式导出所需变量，底层检索模块不读取 dotenv。

本地 Fetch 校验公网 DNS/IP 与逐跳重定向，禁用环境代理及浏览器回退；目标网络必须能直连来源网站。DeepSeek 显式失败不暗中切其他公共搜索供应商，缺 Key 或选项不可用时启动拒绝。账号/Work/Run 冻结预算继续有效，不能用配置切换扩大已有预算。

`config/searxng/settings.yml` 是模板，`config/searxng/entrypoint.sh` 使用 `SEARXNG_SECRET` 和部署 Proxy 设置生成实际配置。`SEARXNG_URL` 告诉 HpAgent 查询地址。

## Work、容量与费用

Work schedule 由 PostgreSQL evaluator 维护，不使用 Temporal Schedule 配置。时间来自不可变 Requirement 的 timing：immediate / once / daily、IANA timezone、once 带 offset。具体 API 见[参考](api.md)。

高级容量参数由使用它的 Python 进程直接读取环境：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `WORK_ACCOUNT_ACCEPT_LIMIT` | `100` | 账户接受的非停止/完成委托上限，具体计数以 WorkCommandService 为准。 |
| `WORK_REPEATED_FAILURE_LIMIT` / `WORK_NO_PROGRESS_LIMIT` | 各 `3` | policy v1 在 Work/revision 首次计数时冻结；范围 1–100，不追改已有窗口。 |
| `WORK_ACCOUNT_COORDINATORS` / `WORK_GLOBAL_COORDINATORS` | `4` / `32` | 有限后台 Run admission。 |
| `CAPACITY_{MODEL,TOOL,FETCH}_GLOBAL` | 各 `12` | 资源票据的全局上限。 |
| `CAPACITY_{MODEL,TOOL,FETCH}_ACCOUNT` | 各 `3` | 每账户资源票据上限。 |
| `CAPACITY_{MODEL,TOOL,FETCH}_INTERACTIVE_RESERVED` | 各 `4` | 全局交互预留；有预留时后台每账户也保留一个槽位。 |
| `WORK_ACCOUNT_SUBAGENTS` / `WORK_GLOBAL_SUBAGENTS` | `6` / `48` | 活跃子分支上限；每次 root 委派仍最多三个。 |
| `DELIVERY_ACCOUNT_CAPACITY` / `DELIVERY_GLOBAL_CAPACITY` | `4` / `32` | 投递发送租约容量，与生产 Run 容量分开。 |

以上高级变量目前未全部列入 `.env.example` 或 Compose environment；只写到项目 `.env` 不保证进入容器。若调优，需通过明确的 Compose environment override 传给所有使用进程，并重建对应容器。不要改变环境后只 restart 并假设新值已应用。

新 Work 的有限预算默认值定义在 [`work_budget.py`](../../src/resources/work_budget.py)，Run 默认值来自 admission。已有预算不能通过改默认配置追改；使用带 Work / budget version、幂等和审计的 budget 命令。模型额度锁顺序为 Account/day → Work → Run，三个账本一次事务预留。RUN_BUDGET_MODE 不能当作提升 Account entitlement 的开关。

## 模型协议与超时

`config/models.yaml` 的 timeout 决定常规模型等待。HTTP connect 为 `min(timeout, 5 秒)`，read 默认沿用 endpoint timeout；Artifact Activity scope 单独覆盖 read 为 `ARTIFACT_MODEL_READ_TIMEOUT_SECONDS`，不改其他聊天调用。

Provider serializer 标识为 `hpagent-provider-request-v2`，OpenAI tool calls 使用 type / function 和 JSON 字符串参数；Anthropic system 为独立字段，工具用 tool_use / tool_result。规范化请求先校验，再冻结快照与预留，不能通过旧原始 transcript 直接请求或覆盖已保存快照。更多失败分类见[可靠性](../architecture/execution.md)。

## 工具参数与运行配置分界

测试隔离标记、脚本备份输出、Benchmark输出与浏览器路径属于开发/运维入口参数，不是应用领域配置；其默认值、帮助及副作用统一维护在[脚本索引](../../scripts/README.md)。变更`.env`或Compose environment后，已有容器不会因`restart`自动获得新的环境，部署操作见[部署指南](../operations/deployment.md)。

### 长期记忆治理连接

`HINDSIGHT_URL` 指定 API 事实查看/预检及 Worker 的同一 Hindsight 目标，
`HINDSIGHT_API_KEY` 可覆盖 Worker 的 `hindsight.api_key`，并供 API 使用。
`hindsight.recall_enabled` 默认 `false`；`HINDSIGHT_RECALL_ENABLED=true/false`
可覆盖它，其他值拒绝启动。关闭 Recall 不停发可靠异步 Retain，也不关闭治理 API。
Compose 显式传入该环境变量，默认仍为 false；改环境后须重建对应应用容器。
本期目标锁定 1.1.0。先按[部署步骤](../operations/deployment.md#记忆核心空库启用)
核验空目标、停旧发送者和在途请求，再显式开放。配置开关不代替切换验收，也不迁移、删除或授权旧数据。
