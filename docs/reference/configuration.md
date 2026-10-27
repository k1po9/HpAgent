# 配置参考

## Agent 工具分配

`config/config.yaml` 的 `agent.default_timezone` 指定 Agent 时间上下文的默认 IANA 时区，默认
`Asia/Shanghai`，非法时区在配置加载时拒绝，不依赖容器本地时区。用户明确指定的时区优先。
Main（Web / QQ）、Generic Work 和子执行在共享 Context Activity 中注入本地时间、偏移、星期和
默认时区；时间基准取 PostgreSQL 保存的 context operation 首次 `started_at`，失败重试不刷新。
聊天另外携带原始触发消息时间，用于解释“明天”“几分钟后”等相对时间，跨午夜重试不改变原意。
后台执行没有原始聊天时间时使用自身执行基准，已确定的 Requirement 时间保持原值。
该时间块随 transcript 和模型输入快照保存，是本次执行的固定基准，不是每轮请求的实时时钟；
新执行重新建立基准，历史 transcript 不补写。修改配置后需重启相关 Worker 才会用于新上下文。

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
| `HINDSIGHT_API_LLM_*` | 否，兼容显式覆盖 | service YAML | Hindsight | 覆盖独立质量文件的 Provider、Base URL、Key 和 Model。 |
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
| `ONEBOT_EXPECTED_SELF_ID` / `HPAGENT_CHANNELS` | QQ 时 | 空（关闭） | Worker | 唯一 bot QQ 号；启用值 `onebot11`，无别名。 |
| `SNOWLUMA_IMAGE` | Beta 时 | 示例固定 digest | SnowLuma | amd64、2.2.0；先用版本检查脚本验证镜像内版本，不能填 latest。 |
| `SNOWLUMA_UID` / `SNOWLUMA_GID` | QQ 时 | 1000 | SnowLuma | 与初始化目录属主一致。 |
| `SNOWLUMA_NOVNC_PORT` / `SNOWLUMA_WEBUI_HOST_PORT` | QQ 时 | 6081 / 5099 | Compose | 只绑定宿主回环。 |
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


### Hindsight 1.1.0 质量参数

连接/超时和 Recall 门控在 [config.yaml](../../config/config.yaml)；质量参数统一在
[hindsight.yaml](../../config/hindsight.yaml)，提示词在 [memory.yaml](../../config/prompts/memory.yaml)。
修改后需要对应 Worker 重载；没有文件监听热更新。目标镜像和 SDK 均固定 1.1.0，
服务端模型不读取 HpAgent 的 models.yaml。

| 配置 | 默认与作用 |
| --- | --- |
| bank.retain_extraction_mode | concise；另支持 verbose/custom/verbatim/chunks。custom 必须配置非空自定义提取文本。 |
| bank.retain_chunk_size / retain_structured_chunk_size | 3000 字符 / null；后者继承原生 chunk_size，不是 token 上限。 |
| bank.retain_max_attachments_per_chunk | 8；当前来源是纯文本聊天，没有内联附件。 |
| bank.entity_labels / entities_allow_free_form | null / true；按 LabelGroup 结构校验，禁止 tag:true 改变现有可信标签范围。 |
| bank.enable_observations / enable_auto_consolidation | true / true；关闭不会自动建立替代归纳调度。 |
| bank.consolidation_llm_batch_size | 8；单次归纳事实批量。 |
| bank.consolidation_source_facts_max_tokens / consolidation_source_facts_max_tokens_per_observation | 4096 / 256；归纳证据预算，可用 -1 表示无限，与 Recall 来源预算独立。 |
| bank.enable_text_search / enable_temporal_retrieval / enable_graph_retrieval / enable_reranking | 均 true；分别控制 BM25、时间、关系和重排路径。 |
| recall.types | world、experience、observation 三类。 |
| recall.budget / max_tokens | mid / 4096；控制检索投入与原生返回文本容量。budget 可选 low/mid/high。 |
| recall.top_n | 5；本地账户/来源许可复核后的最终注入上限。候选不足时允许少于 N，不循环远端请求凑满。 |
| recall.prefer_observations | 显式 false；开启可能抑制 raw facts，随后 Observation 若被本地过滤，不会自动补回被远端抑制的事实。 |
| recall.source_facts.max_tokens / max_tokens_per_observation | 4096 / -1；前者必须为正，后者可为 -1。Observation 来源证据始终请求，不能配置关闭。 |
| recall.min_scores | semantic/keyword/reranker/final 均 null；分路阈值不等于结果交集，也不等于事实正确概率。 |
| recall.include_entities / entities_max_tokens | true / 500；当前 Main 不消费 entity 附加正文，可按评测关闭。 |
| query.mode | hyde；可设 original 跳过 fast 改写。Recall 门控关闭也跳过改写。 |
| query.timestamp_source | trigger_message；来自持久化触发消息 created_at，重试不取新的当前时间。 |
| diagnostics.trace | false；不把原始 trace、query 或记忆正文写日志。 |

原生 RecallRequest 中没有 top_n；Adapter.recall_candidates 返回当前预算内候选，ContextAssembly
先做账户/范围和 MemoryStore 来源许可检查，再按 top_n 选取。只有最终选中的新增文档绑定到
memory_context，历史继承依赖不移除。日志记录远端/Adapter 候选数、来源不完整数、许可拒绝数
和最终注入数；开启 trace 时只提取原生 summary 的六项数值统计（节点/入口/预算/时长/结果数），
丢弃 query、候选正文和 phase details。MemoryItem.scores 保存原生分路分数，relevance 仍只是返回顺序标记。

bank 未列出的键不接管；显式 null 清除该 Bank override、继承全局/租户值。
未知字段、错类型、无效枚举或必要模板缺失拒绝加载；tags、all_strict、逐 item 的
observation_scopes 仍由可信 MemoryScope 生成，不能通过质量文件改写。temporal_window 当前只允许 null，
不提供永久全局日期过滤。旧 agent.recall_top_n 仅在 YAML 中明确出现时作为兼容回退；与新配置
显式冲突时拒绝，0 不表示禁用，请使用 recall_enabled。

PromptsConfig 显式加载 memory.yaml，PromptLoader.get_memory 提供文本：

- retain_mission 指导原生事实提取。用户与第三方的偏好、经历和决定是外部事实 world；助手自身实际经历才是 experience。当前助手回复未经独立核实，不能作为执行完成证据。Retain 不生成 observation。
- observations_mission 指导原生 Consolidation。它替换默认 Mission，系统处理规则和输出格式仍保留，冲突时 Mission 优先。一次性项目决定可以保存，但不自动推广为稳定偏好；重复提取同一来源不算独立证据。
- recall_instruction 通过正式 ContextAssembly 格式化路径进入 Main 的 recalled_memories，说明历史证据的时效、归属和使用规则。无记忆时不追加该段。
- reflect_mission 和 reference_only.reflect 仅记录 inactive；没有原生 Reflect 请求，不用它们驱动归纳。retain_custom_instructions 默认空，仅 custom 模式使用。

旧 config.yaml 的 mission 字段不再接收，长文本不在 Python 中保留另一份默认副本。
新 Bank 按受控 profile 初始化并回读；已有 Bank 不在首次 Retain 时自动覆盖。配置不匹配会使
Recall 降级并阻止新的 Retain；不确定/暂时不可用等待对账，永久 4xx 沿既有规则失败，不能宣称该策略已经应用。同步与回退见
[部署](../operations/deployment.md#记忆质量配置切换)，脚本入口见[脚本索引](../../scripts/README.md#记忆质量配置)。

service.llm 定义全局生成模型，retain_llm/consolidation_llm 为 null 时继承它；embeddings 和
reranker 分别定义向量与重排。当前生成/向量适配为 openai，重排适配为 siliconflow；新 Provider
须扩展显式映射，不能透传未知参数。api_key_env 只保存环境变量名，模型名/地址使用 ${NAME} 引用。
Compose 挂载同一文件，由 Python 桥接器展开到原生 HINDSIGHT_API_* 后 exec 原启动器，不生成
含 Key 的 env 文件。显式已有 HINDSIGHT_API_LLM_* 仍优先，空值回退 YAML；.env.example 不再
放一份重复默认路由。其他高级原生环境覆盖通过明确的 Compose override 注入，不能只改 .env
就假定进入服务。当前 Compose 转发 MiniMax/SiliconFlow 三档模型目录；新增其他变量引用也须显式注入服务。retain_max_completion_tokens 默认 64000，按目标版本要求须大于有效
retain_chunk_size；真实模型输出能力仍需验证。

service.text_search.extension 仅支持 native/pgroonga，省略时为 native。
native_language 仅用于 native，省略时为 english；值须为 PostgreSQL 配置标识符，
pgroonga 配置须省略或设 null，不能用它选择中文分词。原 entrypoint 编译
HINDSIGHT_API_TEXT_SEARCH_EXTENSION 及 native 的
HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE。关键词字段的环境值只接受与 YAML
有效值一致的重复声明，冲突拒绝启动/本地快照生成；不继承模型路由的任意覆盖策略。
该配置不安装扩展、不转换列/索引，也不证明目标已应用。

质量快照保存在既有 memory_actions.request_payload JSON：冻结受控 Bank 参数、Prompt hash、
服务配置 hash 和本地模型标识；不保存凭据或地址，service_remote_verified=false。
后端、native 的有效语言、锁定 API 版本和 Hindsight 镜像身份也进入 config_hash/service_hash。
新增身份会改变旧 profile hash；已有冻结动作不补填、不改写、不换 UUID 重发，
不匹配在既有发送资格之前拒绝。运维关键词检查通过也不把快照的模型远端验证位改成 true。
同一动作重投递复用原快照、hash、UUID；没有快照的历史动作不补填当前值。
Bank 预检在取得来源发送资格前完成，配置读取/初始化失败不把未发送 Retain 标记为已发送未知。来源内容 hash 与质量
profile 分开；发送前 profile 不匹配则保持待恢复，不用当前文件重编译旧动作。Main 的回答指令
单独改变不会使提取 profile 失效。服务实际模型无法通过 Bank Config API 证实，本地文件和
model-map 输出不等于远端模型已验证。Document 验证审计保留原生 memory_unit_count 和
nodes_by_fact_type（有返回时），缺失表示未知，不将 Retain items_count 当成事实数量。


### OneBot11 接入

`channels.onebot11` 包含 host/port/path、access_token_file、expected_self_id、ack/status 超时、
帧/入站队列上限与发送间隔。唯一 bot 使用反向 WS `ws://hpagent:8082/onebot/v11`；
握手校验 Bearer、X-Self-ID、Universal role，再核验 login/version/status。
令牌只从文件读取，不在 YAML 内展开；由初始化脚本生成全局与每账号 snapshot。
Compose 的 `HPAGENT_CHANNELS` 默认空；启用 QQ 时设为 `onebot11` 并填 bot ID，
Web/计算 Worker 可保持空。当前仅支持一个拥有 QQ 连接和投递消费的 Worker。

图片段仅保留可下载的 HTTP(S) 来源到消息 origin 的 `metadata.image_urls`，
本地文件名或宿主路径不会成为下载地址。图片消息入站、来源可读取和视觉模型理解是三个独立检查；
固定回复不能证明已识别图片内容。签名图片 URL 可能过期，不应写入诊断日志或验收记录。
