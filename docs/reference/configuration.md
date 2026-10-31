# 配置参考

## Agent 工具分配

`config/config.yaml` 的 `agent.default_timezone` 指定 Agent 时间上下文的默认 IANA 时区，默认
`Asia/Shanghai`，非法时区在配置加载时拒绝，不依赖容器本地时区。用户明确指定的时区优先。
Main（Web / QQ）、Generic Work 和子执行在共享 Context Activity 中注入本地时间、偏移、星期和
默认时区；时间基准取 PostgreSQL 保存的 context operation 首次 `started_at`，失败重试不刷新。
聊天另外携带原始触发消息时间，用于解释“明天”“几分钟后”等相对时间，跨午夜重试不改变原意。
后台执行没有原始聊天时间时使用自身执行基准，已确定的 Requirement 时间保持原值。
该时间块随 transcript 和模型输入快照保存，是本次执行的固定基准，不是每轮请求的实时时钟；
新执行重新建立基准，历史 transcript 不补写。决策模型每轮请求和 Research 汇总另带
`Asia/Shanghai` 实时时钟，作为调用时刻信息，不替换解释相对时间的固定执行／消息基准；
Research 的冻结提示词保持原值，时钟作为独立 system 消息发送。修改配置后需重启相关 Worker 才会用于新上下文。

[config/agent_tools.yaml](../../config/agent_tools.yaml) 是实际执行的工具分配配置；[config/prompts/agents.yaml](../../config/prompts/agents.yaml) 保存对应中文角色与阶段提示词。两者均按仓库 `src/` 或主 Worker 平铺目录布局定位应用根目录，进程首次读取后缓存；修改后需重启相关 Worker，不热更新已有模型快照和上下文。

- `groups` 定义工具用途、准确 `tools` 名称或动态 `category`（`mcp` / `skill`），每个工具或动态类别只属于一个组。所有原生工具须有归属；拼写错误、未知角色/工具/组、重复 YAML 键及不支持的授予方式直接报错，不回退到宽松策略。
- `roles.<角色>.groups` 决定该角色允许的工具组。关闭某角色的研究能力，可从该角色列表移除 `research`，仍保留组定义供其他角色使用；关闭代码写入同理移除 `code_write`，Shell 需另外移除 `shell`。分配不会创建实现或跳过服务、渠道、文件范围、预算与审批检查。
- `exposure=existing` 保留工具原有常驻、文件入口或按需检索规则；`always` 改为满足运行条件后常驻；`semantic` 改为按需检索。检索总上限仍生效，常驻/入口工具过多时报配置错误。复杂能力、联网、结果管理和委派属于根执行追加工具，只支持 `always`，通过移除角色的组来关闭。
- Main 与 Worker 的 `grant=configured` 从配置组取工具，但不能越过服务端角色边界：Main 不获得后台分支委派或 Work 专用文字发布，Worker 不获得 Work 管理；只读子 agent 使用 `parent_read_only_subset`，不能改成自行授予全部工具。父侧填写的分支工具仍须通过服务器核验，文件/目录引用不等于授权。
- Research 合成、HTML 生成、规划、评估、工具摘要和记忆查询改写模型固定为 `grant=none`、空工具组；它们使用固定服务端流程，不因配置存在而变成工具循环 agent。MCP/Skill 工具不能以原生或专用工具的同名名称跨类别取得分配。

配置减少权限后，新的工具使用按当前分配检查；已提交的工具回执不会重新执行，已登记的复杂能力实例按原不可变契约恢复，不因配置变化重新发送模型请求。修改提示词时对照组用途和角色列表；真正发给模型的本轮工具清单还受服务和检索限制，不能把允许列表理解成始终可用。

首次上线的取值决定集中在[配置确认表](../operations/first-release-configuration.md)，Compose 可插值的全部变量集中在[.env.example](../../.env.example)。本页维护各配置入口的语义。

Worker 的已支持环境覆盖在 `config/config.yaml` 之后应用；并非任意环境变量都会覆盖同名 YAML。
主 Worker 入口读取项目 `.env`，已有进程环境优先；API 和 Document Worker 直接读取进程环境。
Compose 的 `.env` 用于插值，只有服务显式声明的变量才进入容器。Model YAML 通过 `${NAME}`
引用环境变量，Secret 不写入 YAML。

### 配置输入安全与当前生效边界

`HPAGENT_ENV` 支持 `development`、`test`、`production`，统一去除两端空白并忽略大小写；
未知值、空值拒绝启动，不能因 `Production` 或拼写错误绕过生产检查。API 的直接 Settings
构造同样执行生产安全检查。API 布尔环境变量只接受大小写不敏感的 `true` / `false`，
不接受两端空白、`1`、`yes` 或拼写错误；Worker 文件能力保留原有去除空白规则。

主配置、模型配置、通用提示词及记忆质量配置拒绝重复 YAML 键、非文本映射键和递归别名；
合法 YAML merge 保持原有覆盖顺序。已有配置中的错误结构、已声明字段的错误类型、
非有限数值与无效数量/超时在加载时拒绝；Worker 支持的环境覆盖后再次校验。
API 的 SSE 容量和周期必须为正，测试执行器延迟允许零、不允许负数；Cursor Key Ring
必须为不含重复键的文本值对象。错误只报告字段与约束，不回显输入值或原始 YAML 行。
提示词文件存在但无效时直接失败，不再静默使用空提示词；缺失文件仍按各原有入口处理。

主配置和模型配置的已加载 section 拒绝未知字段，错误指向完整字段路径；先将旧文件中的
拼写错误与下表中的已知兼容项分开处理。Provider 的 `extra_body` 仍是开放扩展映射，
通用提示词的具名模板仍按原入口读取，记忆质量与工具分配沿各自既有 schema 校验。
已知停用字段继续读取并记录不含值的 `ignored` 告警，不因保留旧字段而增加执行行为。
当前须区分：

| 配置类别 | 来源与生效边界 |
| --- | --- |
| 部署配置 | 地址、凭据、目录、容量等由各进程读取；改环境须重建对应容器，改文件须重启消费进程。它们不是 Run 策略快照。 |
| 新执行策略 | Agent 的轮次、时区与三项工具摘要参数在新 Run admission 写入 input_snapshot.agent_policy；上下文、记忆及模型输入继续沿各自原有冻结边界。历史缺少策略的 Run 保留旧读取路径，不补填。 |
| 持久能力策略 | Research 新调用使用 research-web-v3，将局部执行策略写入既有 Definition/Invocation；阶段重试读取原快照。research-web-v2 保留已知原定义与旧读取语义，不追溯补填。Execution、预算及阶段事实继续沿原身份恢复。 |
| 当前安全约束 | 账号状态、授权撤销、revision、fence、幂等与事务约束按使用和提交边界实时复验；不随业务策略冻结，也不能通过配置关闭。 |

Agent 与 Research 复用既有不可变事实，不新增策略表或覆盖历史模型输入；细则如下。

### 持久策略来源与生效规则

API 与 QQ/Worker 从同一 `config/config.yaml` 的 `agent` section 初始化强类型策略。
API 只读该 section，不加载模型凭据或启动 Worker；Worker 使用已加载的同一字段。
代码缺省 `max_tool_turns=20`，标准 YAML 显式值为 `5`；显式值优先。
`default_timezone`、`tool_result_summary_enabled`、`tool_result_summary_threshold`、
`tool_result_summary_max_chars` 的代码缺省分别为 `Asia/Shanghai`、`true`、`4000`、`1000`。
修改文件后须重启对应命令进程，影响随后新建的 Agent Run；执行端按 Run 快照消费，
不以自己启动时的默认值覆盖。固定专业 Run 不增加无消费者的 Agent 策略。

同一 Run 的 Activity retry、等待后恢复和 Worker 接管使用同一策略；Subagent 沿用所属
Run 的时区和摘要参数，轮数仍为代码定义的 6。显式 `retry_run`、Work retry/advance 创建的
新 Run 是新 admission，冻结当次配置；同幂等键重投只返回原已创建 Run。
旧 Run 没有 `agent_policy` 时保留原路径并告警，不推断或回填过去配置；已有 Workflow 输入
中的轮数仍按 History 恢复。策略字段存在但形状、类型或版本错误时拒绝，不回退当前配置。

Research 标准组合的新调用冻结当前阶段阈值（30 个来源、20 次抓取、3 的缺口诊断阈值、
3 个证据和 2 个独立来源）、显式搜索/抓取 provider、15/20 秒等待策略，以及准确的合成
prompt/hash、`chat` 用途和 4096 输出 token 参数。实际流程图仍最多三轮；`max_iterations`
不新增控制 Workflow 轮数的行为。提取、历史、检查与流程图由已知实现版本约束，不逐常量配置化。
现有时限/模型调用/工具调用/重试值保持 3600/1/23/4，既有预算不会增加。

冻结的检索 provider 不跟随当前默认切换；当前服务/Key 不可用时明确失败。内层 HTTP 超时
也使用冻结值；GitHub 自身固定等待边界保持原专业实现。DeepSeek fetch 仍受最终 URL
可验证性约束，不因接线而开放。服务地址和凭据继续使用当前部署值，不写入业务策略。
底层模型 endpoint 在每个请求准备边界选择，沿原 Model Input Snapshot 冻结；这不承诺跨
不同模型调用固定底层 Provider。当前 entitlement/tier、grant、记忆撤回、revision 和 fence
始终重新校验，快照不提供永久授权。

对外 `research` v1 schema 与工具名不变。旧 `research-web-v2` 无策略快照，保留其已知原定义
和旧实例参数语义；新 `research-web-v3` 从原 descriptor 读取，不要求与当前策略值相等。
未知实现、越权定义或损坏 policy/hash 拒绝执行。父登记恢复复用原 Invocation/Execution、
scope 与 Operation，不重算候选或重做已提交阶段；响应未知时不换键重发。

没有策略的 CapabilityDefinition 序列化仍省略新增字段，旧 JSON/digest 不变。带策略的新
描述在原 PG 65536 字节上限内校验，不截断 prompt/inputs；合成 prompt 上限 16384 UTF-8
字节，超限在冻结前拒绝。原输入 schema、资源范围与预算不放宽。

未来发布前须保证所有执行端已支持新描述，避免混用不识别它的旧 Worker；回退旧二进制
不能剥除策略或降级持久事实，应先停止新写入并处理/保留新版本执行。此约束不触发自动部署。
本批保持现有 Workflow 输入 schema、队列、ID 与命令图；HTML 大小策略、全部记忆/工具检索
策略和跨模型调用的路由固定仍不属于本批 admission 冻结范围。

### 配置来源、缺省与兼容

Python 配置类型是常规缺省来源；`config/config.yaml` 保存显式部署选择，
`config/models.yaml` 保存模型路由与参数覆盖。加载器直接构造相应类型，不重复写入相同缺省值。
`.env.example` 和 Compose 的数值是部署预设，不能将它们当作宿主 Python 进程的默认值。
例如仓库 `agent.max_tool_turns=5` 明确覆盖代码缺省 20，仍保持原执行轮次。

| 入口 | 来源与优先关系 |
| --- | --- |
| Worker 主配置 | dataclass 缺省 → `config/config.yaml` 显式值 → 已支持的环境覆盖。相对 YAML 数据路径锚定配置目录的父目录；随后覆盖的相对环境路径仍沿旧规则解释，建议使用绝对路径。 |
| 模型文件 | `from_yaml(..., models_path=...)` 显式参数 → `HPAGENT_MODELS_PATH` → 主配置同目录的 `models.yaml`。`${NAME}` 从进程环境展开，不另读 dotenv。模型级 `extra_body` 递归覆盖 Provider 层。 |
| 模型链格式 | 支持顶层 `chat` / `fast` 等链，以及旧 `models: {chat: ...}` 包装；混用时旧包装优先并告警，不合并两套链。 |
| API | `WebApiSettings` 声明的常规缺省 → 进程环境；生产 Origin、签名材料等仍有显式配置门禁。直接构造 Settings 不读取环境。API 的 Hindsight 直构默认不连接，环境入口保留 `http://hindsight:8888` 的既有连接预设。 |
| 固定 Workflow 参数 | `run_lifecycle_contracts.py` 是队列与生命周期窗口的值来源；配置声明引用这些常量，启动门禁拒绝不同值，不作为普通调优参数。 |

工具数量仍有一项保留的兼容分歧：直接构造 `ToolRagConfig` 缺省为 12，加载模型 YAML
而省略 `tool_rag.top_k` 时沿历史规则为 8；标准模型 YAML 显式选择 12。
统一为任一数字都会改变一类入口的合法行为，因此尚未统一。需要稳定数量时显式设置
`tool_rag.top_k`；该值经过现有组合进入工具检索，重排 `top_n` 由检索请求的 limit 决定。

| 旧字段或 section | 当前处理与迁移方向 |
| --- | --- |
| `scheduler`、`session` | 主 YAML 兼容忽略并告警；Work 调度及 Conversation 状态由 PostgreSQL 持有。 |
| `agent.mode`、`idle_timeout_minutes`、`event_fetch_limit`、`activity_timeout`、`archive_timeout`、`multi_agent` | 兼容忽略并告警；执行策略来自 Run，超时/委派遵循现有版本契约，不恢复旧 Session 或多 Agent 开关。 |
| `agent.max_history_turns` | 不控制当前上下文；历史选取由现有 ContextAssembly token 预算决定。 |
| `agent.reflect_interval_hours`、`hindsight.reflect_timeout` | 不启动全 Bank Reflect 调度或原生 Reflect 请求。 |
| `workspace.db_path`、`sandbox.result_ttl` | 当前执行链不消费；Workspace 元数据仍使用 PostgreSQL。 |
| 模型文件的 `channel_overrides`、`rerank.top_n` | 从标准 YAML 删除，旧文件仍兼容读取并告警；不向 Brain 或重排调用新增接线。正式对话输出长度/等待预算修改对应模型链参数。 |
| 主 YAML 内的 `models`、`prompts`、`memory_quality` | 兼容忽略并告警；分别使用独立模型文件、提示词目录、记忆质量文件。 |
| 主 YAML 内的 `research` | 当前不加载该 section，兼容忽略并告警；只有已有环境覆盖和代码缺省生效。增加接线前须区分连接参数与持久研究策略，不能直接使旧执行读取新阈值。 |

`agent.recall_top_n` 是仍被消费的兼容入口，与上述停用项不同；明确提供时沿既有冲突校验
映射到记忆质量 `recall.top_n`，不能通过字段清理将其静默丢弃。

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
| `HPAGENT_MODELS_PATH` | 否 | 宿主省略时同目录；Compose 为 `/app/config/models.yaml` | Worker | 替代 Model Config 路径；模板中的容器路径不能直接当作宿主路径使用。 |
| `WEB_PUBLIC_ORIGIN` | 对外服务时必需 | Compose 中为 `https://localhost` | API | 允许的浏览器 Origin。 |
| `WEB_COOKIE_SECURE` | 生产必需 | Python `true`；基础 Compose `false`；Beta `true` | API | Secure Cookie 开关。 |
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
| `WEB_FILE_UPLOAD_ENABLED` | 否 | Python `false`；Compose `true` | API/Worker | Upload 与不可变成果 publisher 开关；Research/HTML/普通 Markdown 交付都需要 publisher。 |
| `WEB_FILE_TRANSFORM_ENABLED` | 否 | Python `false`；Compose `true` | API/Worker | 文件格式转换开关；普通 Markdown 发布不要求开启转换。 |
| `WEB_FILE_SHELL_ENABLED` | 否 | `false` | API/Worker | File Shell 能力开关。 |
| `FILE_STORE_ROOT` / `FILE_RUN_ROOT` / `DOCUMENT_RUN_ROOT` | Compose 管理 | Volume 路径 | File/Document | 长期文件字节与隔离执行目录；目录/授权/修订在 PostgreSQL。 |
| `FILE_MAX_BYTES` | 否 | Python `134217728`；Compose `33554432` | File | 单文件大小上限。 |
| `FILE_DIRECT_READ_MAX_BYTES` | 否 | `1048576` | File | Direct Read 阈值。 |
| `FILE_MAX_COUNT_PER_MESSAGE` | 否 | Python `10`；Compose `5` | API | 每条消息上传数量上限。 |
| `FILE_CLEANUP_INTERVAL_SECONDS` | 否 | `300` | Worker | 文件清理轮询周期，必须为正；不会改变授权或 GC 引用检查。 |
| `GOTENBERG_URL` | 否 | Compose 中为 `http://gotenberg:3000` | File | 转换服务 Endpoint。 |
| `SEARXNG_URL` | 否 | Compose 中为 `http://searxng:8080` | Research | 搜索 Endpoint。 |
| `SEARXNG_SECRET` | 部署必需 | 开发值 | SearXNG | 搜索服务 Secret。 |
| `ARTIFACT_MODEL_READ_TIMEOUT_SECONDS` | 否 | `90` | Worker | HTML 成果生成独立读取预算，合法范围 5–300 秒；连接预算最多 5 秒。 |
| `HPAGENT_MIGRATIONS_DIR` | 宿主机/CI 建议显式设置 | 部署挂载路径 | API/Worker/Migrate/Test | 指向完整 `persistence/migrations`；Compose 使用 `/opt/hpagent/migrations`，CI 使用仓库绝对路径。 |
| `RUN_BUDGET_MODE` | 否 | 按入口；Compose `enforce` | Worker/API | 入口差异见下方费用说明，不能用省略变量假定所有进程相同。 |
| `LOG_LEVEL` | 否 | `INFO` | Python Service | Console Log Threshold。 |
| `LOG_DIR` | 否 | `.data/logs` | Python Service | Structured Log 目录。 |
| `WEB_GATEWAY_PORT` | 否 | `80` | Gateway | 对外 Host Port。 |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | 否 | 空/本地服务列表 | Build/Runtime | Proxy 路由。 |

`.env.example` 是部署模板。Source Settings 还提供 SSE Buffer、Outbox Recovery、Cleanup Interval 和 Budget Policy 等调优变量；没有运维证据时应保留代码默认值。

### 运行时参数的实际消费者

部署容量、文件限制与既有 HTML 运行限制定义在
[`common/runtime_config.py`](../../src/common/runtime_config.py)，由小型强类型对象读取。
Worker 在取得依赖与工作区锁前检查这些输入；API 检查自身 Settings 与容量输入后才连接数据库。
配置对象不缓存环境，不创建另一套策略账本；消费者保留原有读取时机。

| 参数 | 消费者与生效边界 |
| --- | --- |
| Work 接受量、协调数、子分支/能力/投递容量 | 现有命令准入、Execution 准入、资源票据和 Delivery claim 读取当前进程环境；已持有票据、已提交结果和预算事实不回写。 |
| `FILE_MAX_BYTES` / `FILE_MAX_COUNT_PER_MESSAGE` | 前者进入 API 上传与 Worker publisher，后者是 API 消息附件数量检查；不成为 Agent 生成成果数量上限。 |
| `FILE_DIRECT_READ_MAX_BYTES` | 创建 Worker 文件读取工具时取值；默认整文件直读超阈值时拒绝，提示改用有界读取，不扩大文件授权。 |
| `FILE_CLEANUP_INTERVAL_SECONDS` | 启动 Worker 文件清理循环时取值；改环境须重建消费容器。 |
| `ARTIFACT_MODEL_READ_TIMEOUT_SECONDS` | Worker 组装既有 HTML generator 时取值，沿原 model call scope 控制读取等待；Workflow 窗口和重试契约不变。 |
| `ARTIFACT_HTML_MAX_BYTES` | 保留既有裸进程或显式 Compose override 的环境读取，并校验正整数；标准 Compose 不透传。它影响持久成果生成，扩大调整入口须先有完整冻结兼容方案。 |
| `QQ_BINDING_CHALLENGE_SECONDS` | API/Worker 共用缺省与正整数校验，建立新绑定挑战时使用；既有挑战的过期事实不回写。 |
| `RUN_BUDGET_MODE` | 各入口明确保留自己的缺省；新预算沿原 admission 冻结，已有预算及权限规则不因当前配置重写。 |

新增可选 Compose 透传如下，未设置的变量保持未设置，Python 继续使用原缺省。
Compose 静态 JSON 可将这些未设置项表示为 `null`，不等于向容器写入字符串 `"None"` 或空值。

| 消费进程 | 可选变量 |
| --- | --- |
| API、主 Worker | `HINDSIGHT_API_KEY`、`WORK_ACCOUNT_ACCEPT_LIMIT`；`HINDSIGHT_URL` 共同覆盖时仍沿已有目标切换流程，默认保持内部服务地址。 |
| 主 Worker | `TEMPORAL_TASK_QUEUE`、`WEB_OUTBOX_LEASE_TIMEOUT_SECONDS`、`WEB_OUTBOX_RECOVERY_INTERVAL_SECONDS`、`WORK_ACCOUNT_SUBAGENTS`、`WORK_GLOBAL_SUBAGENTS`、`CAPABILITY_ACCOUNT_EXECUTIONS`、`CAPABILITY_GLOBAL_EXECUTIONS`、`DELIVERY_ACCOUNT_CAPACITY`、`DELIVERY_GLOBAL_CAPACITY`。`TEMPORAL_TASK_QUEUE` 只影响既有 Memory 指标队列，不改变固定 Agent 队列。 |
| API | `WEB_CURSOR_KEYS_JSON`（配合 `WEB_CURSOR_ACTIVE_KEY_ID`）、`WEB_SSE_HANDSHAKE_BUFFER_EVENTS`、`WEB_SSE_HANDSHAKE_BUFFER_BYTES`、`WEB_SSE_KEEPALIVE_SECONDS`、`WEB_SSE_MAX_CONNECTIONS`、`WEB_TERMINAL_PUBLISHER_POLL_SECONDS`、`LOG_LEVEL`。 |

API 不再透传无人消费的文件直读/清理变量；Document Worker 使用只读 TenantFileReader，
不再透传无人消费的 `FILE_MAX_BYTES`。这不增加文档执行、上传或成果发布权限。
上述接线只改变显式配置能否到达消费者；已有私有 `.env` 若包含此前无效的显式值，新部署后
可能开始生效，需核对实际目标配置，不能把合成默认基线当作线上验证结果。

## 模型配置

完整的用途、供应商、变量、调用位置和脱敏检查见[模型分工表](model-routing.md)。Research报告与HTML成果共用chat，长期记忆是独立的Hindsight配置，reasoning不自动被业务选用。

`config/models.yaml` 定义 Provider，以及 `fast`、`chat`、`embedding`、`image`、`reasoning` 的有序 Model Chain 和一个 Reranker。Provider Entry 包含 API Format 和环境变量引用，凭据只保存在 `.env`。同一文件还声明 Tool Retrieval、MCP Config Path 和 Skill Path；旧 Surface Override 不参与正式调用。

模型 YAML 条目接受非空文本 `access_tier`，省略时仍为 `standard`；该字段进入已有
ModelEndpoint / CredentialManager / ResourcePool 权限校验，不放入供应商 `extra_body`。
普通 Account 的 `account_entitlements.model_access_tier` 必须与端点 tier 精确匹配，
`owner` 可以访问所有 tier。配置端点 tier 不授予账号权益，邀请码 `--profile` 也不会
自动创建端点。账号与端点访问资格属于当前安全检查，新请求和重试仍检查当前权威权益；
原模型快照与回执不回写，不承诺变更 tier 后旧执行仍有访问资格。

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

| 变量 | Python 缺省 | Compose 部署预设 | 用途 |
| --- | --- | --- | --- |
| `WORK_ACCOUNT_ACCEPT_LIMIT` | `100` | 可选透传 API/Worker | 账户接受的非停止/完成委托上限，具体计数以 WorkCommandService 为准。 |
| `WORK_REPEATED_FAILURE_LIMIT` / `WORK_NO_PROGRESS_LIMIT` | 各 `3` | 未透传 | policy v1 在 Work/revision 首次计数时冻结；范围 1–100，不追改已有窗口。 |
| `WORK_ACCOUNT_COORDINATORS` / `WORK_GLOBAL_COORDINATORS` | `4` / `32` | `3` / `16` | 有限后台 Run admission。 |
| `CAPACITY_{MODEL,TOOL,FETCH}_GLOBAL` | 各 `12` | 各 `8` | 资源票据的全局上限。 |
| `CAPACITY_{MODEL,TOOL,FETCH}_ACCOUNT` | 各 `3` | 各 `2` | 每账户资源票据上限。 |
| `CAPACITY_{MODEL,TOOL,FETCH}_INTERACTIVE_RESERVED` | 各 `4` | 各 `3` | 全局交互预留；有预留时后台每账户也保留一个槽位。 |
| `WORK_ACCOUNT_SUBAGENTS` / `WORK_GLOBAL_SUBAGENTS` | `6` / `48` | 可选透传 Worker | 活跃子分支上限；每次 root 委派仍最多三个。 |
| `CAPABILITY_ACCOUNT_EXECUTIONS` / `CAPABILITY_GLOBAL_EXECUTIONS` | `4` / `32` | 可选透传 Worker | 独立能力执行容量，与子分支、Work 协调和资源票据分别检查。 |
| `DELIVERY_ACCOUNT_CAPACITY` / `DELIVERY_GLOBAL_CAPACITY` | `4` / `32` | 可选透传 Worker | 投递发送租约容量，与生产 Run 容量分开。 |

表中容量参数通过纯配置模块 `common/runtime_config.py` 的 `CapacityConfig` 读取，
API/Worker 在基础设施 I/O 前校验；消费者仍在原准入/资源申请边界读取，不缓存到 Run。
参数允许非负整数：零保留原有停止相关准入的语义，不取消已有执行或撤销已持有票据。
交互预留继续沿原算法扣减，预留超过全局容量时后台容量为零，不新增另一套容量或锁规则。

“可选透传”没有 Compose 数字 fallback，省略时使用 Python 缺省；显式设置后须重建消费容器。
旧私有 `.env` 若已写入此前未透传的变量，新部署后这些值会开始生效，发布前需离线核对；
不能用未改动模板推断私有配置仍等于缺省。`WORK_REPEATED_FAILURE_LIMIT`、
`WORK_NO_PROGRESS_LIMIT` 及预算 policy version 未通过标准 Compose 透传，仍须按持久策略契约处理。

`RUN_BUDGET_MODE` 的裸进程缺省仍按既有入口区分：API Settings 与 QQ Conversation
命令入口使用 `observe`，共享 Worker ResourcePool 和 Work due loop 使用 `enforce`；
Compose 对主 Worker/API 显式传入 `enforce`。这里保持原行为，不将一个入口的缺省套用到其他入口。

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
