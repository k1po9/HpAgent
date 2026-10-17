# 首次上线配置确认表

状态基线：2026-10-17。首次上线使用空库，不进行旧业务数据转换。此表是本轮唯一的配置决策入口；按编号逐项填写“确认结果”。建议值在确认前不改变当前运行环境。

实际运行配置仍由 `.env`、Compose、`config/config.yaml`、`config/models.yaml` 及 MCP/搜索配置加载。本表不新增配置加载器，也不复制真实密钥；确认结果应在后续环境落地时写回对应来源。

## 逐项确认

可直接回复：`E01=...，N01=...，M01=...`；涉及密钥仅确认“已生成/由哪个 Secret 管理”，不填密钥值。

| 编号 | 配置项 | 仓库当前值/事实 | 建议及需要决定的内容 | 落地位置 | 确认结果 |
| --- | --- | --- | --- | --- | --- |
| `E01` | 上线机器 | 尚未指定 CPU / 内存 / 磁盘 / OS | 填写服务器规格、部署目录与可用空间 | 部署环境 | 待确认 |
| `E02` | 首发范围 | Compose 有 web-prod / qq / tools；config 默认启用 napcat | 先明确 Web、QQ、Research、长期记忆及文件能力的启用范围 | docker-compose.yaml；config/config.yaml | 待确认 |
| `E03` | 发布模式 | 主 Worker / API / 初始化服务仍挂载宿主 src | 生产使用固定版本镜像，去掉源码覆盖；开发继续保留挂载 | docker-compose.yaml / 生产 overlay | 待确认 |
| `E04` | 环境隔离 | 基础设施服务无 profile；命名卷跟随 Compose project | 生产使用独立 project、目录和卷，不能沿用日常库 | Compose project / volumes | 待确认 |
| `E05` | 数据库起点 | 首次上线，旧开发/测试数据可丢弃 | 新建空数据库/卷，执行完整 schema 初始化；无需旧库转换 | persistence/docker-init；persistence/migrations | 已确认 |
| `N01` | 域名与 Origin | 模板为 http://127.0.0.1:5173 | 填写最终 https://域名（有非默认端口时含端口），无路径 | WEB_PUBLIC_ORIGIN | 待确认 |
| `N02` | TLS 与网关 | web-gateway 仅监听容器 HTTP 80；宿主端口默认80 | 确认 TLS 终止点、证书续期、宿主绑定和 SSE 代理 | WEB_GATEWAY_PORT；web/nginx.conf；外部反代 | 待确认 |
| `N03` | 生产认证 | HPAGENT_ENV=development；WEB_COOKIE_SECURE=false | 生产分别设 production / true；API 强制 HTTPS Origin | HPAGENT_ENV；WEB_COOKIE_SECURE | 待确认 |
| `N04` | 网络与代理 | 基础设施/API多数 loopback；可选 HTTP(S)_PROXY | 确认服务端直连或代理，NO_PROXY 覆盖所有内部服务 | HTTP_PROXY；HTTPS_PROXY；NO_PROXY | 待确认 |
| `S01` | 应用数据库凭据 | 模板3个角色密码均为 change-me | 3个独立随机密码；避免直接使用需额外 URL 编码的字符 | HPAGENT_MIGRATE_PASSWORD；HPAGENT_API_PASSWORD；HPAGENT_WORKER_PASSWORD | 待确认 |
| `S02` | 认证签名材料 | 模板为 change-me / 开发占位值 | 独立生成 cursor / session / CSRF / QQ binding secret，各至少32字节；不在表内填值 | WEB_CURSOR_SECRET；WEB_SESSION_TOKEN_PEPPER；WEB_CSRF_SIGNING_KEY；QQ_BINDING_CODE_PEPPER | 待确认 |
| `S03` | 内部数据库凭据 | Temporal / Hindsight 密码在 Compose 中固定 | 确认并参数化内部数据库密码，更新对应消费者；不能只改一个端点 | docker-compose.yaml：temporal-postgres/temporal/hindsight-postgres/hindsight | 待确认 |
| `S04` | 开户与权益 | 自助注册默认 standard，每日50000 token；邀请码支持预置权益 | 确认公开注册还是邀请准入、首个管理员和首发额度；准入策略变更需实现 | account 服务；邀请码运维入口；docs/operations/account-governance.md | 待确认 |
| `M01` | 主对话/推理模型 | chat/reasoning 使用 MiniMax；默认注册档位模型是否匹配仍需核实 | 确认 Provider、Base URL、模型名、可用档位和供应商限额 | config/models.yaml；MINIMAX_* | 待确认 |
| `M02` | 摘要与回退链 | fast 为 MiniMax → SiliconFlow → Alibaba | 逐个确认保留的提供商及其模型；不用的条目从链和 providers 一并移除 | config/models.yaml；SILICONFLOW_*；ALIBABA_BAILIAN_* | 待确认 |
| `M03` | 向量与重排 | embedding / rerank 使用 SiliconFlow，tool_rag.enabled=true | 确认 embedding/rerank 型号、费用及工具检索是否开启 | config/models.yaml；SILICONFLOW_* | 待确认 |
| `M04` | 长期记忆 | hindsight.enabled=true；Hindsight 自己配置 LLM/embedding/rerank | 确认记忆启用、模型、数据保留；当前 Compose 启用服务不随 YAML 自动裁剪 | config/config.yaml；HINDSIGHT_API_LLM_*；docker-compose.yaml | 待确认 |
| `M05` | 未配齐的图像链 | image 引用 openai，但 providers 未定义 openai | 首发不提供图像生成则明确关闭/移除该链，否则补齐 provider | config/models.yaml：image / providers | 待确认 |
| `M06` | 其他模型与超时 | DeepSeek 变量预留；成果读取超时90秒；模型路径支持覆盖 | 确认是否启用 DeepSeek、成果超时与链级超时 | DEEPSEEK_*；ARTIFACT_MODEL_READ_TIMEOUT_SECONDS；HPAGENT_MODELS_PATH | 待确认 |
| `F01` | 文件能力 | 上传开；transform/shell关；预算 enforce | 确认上传、格式转换；生产 Web host shell 必须关闭 | WEB_FILE_UPLOAD_ENABLED；WEB_FILE_TRANSFORM_ENABLED；WEB_FILE_SHELL_ENABLED；RUN_BUDGET_MODE | 待确认 |
| `F02` | 文件限额 | 单文件128MiB；每消息10个；直读1MiB；清理周期300秒 | 逐个确认并与网关请求体限制对齐 | FILE_MAX_BYTES；FILE_MAX_COUNT_PER_MESSAGE；FILE_DIRECT_READ_MAX_BYTES；FILE_CLEANUP_INTERVAL_SECONDS | 待确认 |
| `F03` | 文件与执行目录 | File Store/Run/Document 使用各自卷；workspace 在 .data | 确认磁盘路径、配额、保留周期、所有权和备份位置 | docker-compose.yaml：volumes；WORKSPACE_ISOLATION_MODE | 待确认 |
| `X01` | 渠道 | config/config.yaml 默认 napcat；qq profile 控制可选容器 | Web 首发若不启用 QQ，必须同步取消渠道配置；不能只不启动 qq profile | config/config.yaml：channels.enabled；NAPCAT_*；QQ_OFFICIAL_* | 待确认 |
| `X02` | MCP 外部工具 | servers.yaml 含远程端点和 stdio 工具；auto_connect=true | 逐个确认 server、外部凭据、数据访问和可执行工具 | config/mcp/servers.yaml；config/models.yaml：mcp | 待确认 |
| `X03` | 工具与沙箱 | native_tools_enabled=true；nsjail_enabled=false；Skills开启 | 明确代码/命令执行权限、隔离方式及可用 Skill | config/config.yaml：sandbox；config/models.yaml：skills | 待确认 |
| `X04` | 搜索与文档转换 | SearXNG / Gotenberg 内部URL及镜像摘要已配置 | 确认启用、SEARXNG_SECRET、真实搜索和文档任务可用性 | SEARXNG_*；GOTENBERG_*；config/searxng | 待确认 |
| `C01` | 后台并发 | 账户4、全局32；资源默认全局12、交互保留4、账户3 | 按服务器与供应商额度决定；这些源码变量当前未在 Compose 中透传 | src/resources/capacity.py：WORK_* / CAPACITY_* | 待确认 |
| `C02` | Lease、超时和恢复 | Execution lease900秒；Research lease及各Activity超时分散在实现 | 先按现值记录；用真实 Worker 验证取消、故障接管及资源释放后再调整 | AGENT_EXECUTION_LEASE_TTL_SECONDS；runtime/Activity 配置 | 待确认 |
| `O01` | 镜像与基础版本 | 应用默认 :offline；NapCat为latest；Python混用3.11/3.12 | 选择发布标签/摘要及支持的Python版本；固定已验证组合 | HPAGENT_*_IMAGE；WEB_GATEWAY_IMAGE；Dockerfile；docker-compose.yaml | 待确认 |
| `O02` | 日志与监控 | LOG_LEVEL=INFO；日志在.data；部分服务有轮转 | 确认保留天数、容量、磁盘告警、API/Worker/Document监控 | LOG_LEVEL；Compose logging；健康检查 | 待确认 |
| `O03` | 备份和恢复 | 现脚本备份 App/Hindsight PG与Git workspace；缺Temporal/File Store一致性快照 | 首次空库不需导入旧备份；投产后的恢复点、备份存储和恢复演练仍需确定 | scripts/operations/backup.sh；docs/operations/backup-restore.md | 待确认 |

## 环境变量集中入口

[根 .env.example](../../.env.example) 已补齐当前 Compose 的全部66个插值变量，包括此前缺失的安全Cookie、文件限额、预算模式、模型路径及应用镜像引用。模板继续保留开发默认值；生产修改以本表确认结果为准。

下面从仓库中的 Compose 与示例模板核对，不读取本机 `.env`。有些开关、目录和数据库端点在 Compose 中写死；写入 `.env` 并不会自动覆盖这些值，必须修改 Compose/overlay。容量参数属于此类未透传问题。

| 变量 | 示例模板默认值 | Compose 使用的缺省/要求 |
| --- | --- | --- |
| `AGENT_EXECUTION_LEASE_TTL_SECONDS` | 900 | 900 |
| `ALIBABA_BAILIAN_API_KEY` | 空（待填） | 未提供缺省 |
| `ALIBABA_BAILIAN_BASE_URL` | 空（待填） | 未提供缺省 |
| `ALIBABA_BAILIAN_FAST_MODEL` | 空（待填） | 未提供缺省 |
| `ARTIFACT_MODEL_READ_TIMEOUT_SECONDS` | 90 | 90 |
| `DEEPSEEK_API_BASE_URL` | https://api.deepseek.com | 未提供缺省 |
| `DEEPSEEK_API_KEY` | 空（待填） | 未提供缺省 |
| `DEEPSEEK_FLAGSHIP_MODEL` | 空（待填） | 未提供缺省 |
| `DEEPSEEK_HIGHSPEED_MODEL` | 空（待填） | 未提供缺省 |
| `DEEPSEEK_STANDARD_MODEL` | 空（待填） | 未提供缺省 |
| `FILE_CLEANUP_INTERVAL_SECONDS` | 300 | 300 |
| `FILE_DIRECT_READ_MAX_BYTES` | 1048576 | 1048576 |
| `FILE_MAX_BYTES` | 134217728 | 134217728 |
| `FILE_MAX_COUNT_PER_MESSAGE` | 10 | 10 |
| `GOTENBERG_IMAGE` | gotenberg/gotenberg@sha256:87c16b9f364279d321bc9772d31fa58aa6abe036423c270698bd636c3a8e9466 | gotenberg/gotenberg@sha256:87c16b9f364279d321bc9772d31fa58aa6abe036423c270698bd636c3a8e9466 |
| `GOTENBERG_URL` | http://gotenberg:3000 | http://gotenberg:3000 |
| `HINDSIGHT_API_LLM_API_KEY` | 空（待填） | 未提供缺省 |
| `HINDSIGHT_API_LLM_BASE_URL` | 空（待填） | 未提供缺省 |
| `HINDSIGHT_API_LLM_MODEL` | 空（待填） | 未提供缺省 |
| `HINDSIGHT_API_LLM_PROVIDER` | 空（待填） | 未提供缺省 |
| `HPAGENT_API_IMAGE` | hpagent/hpagent-api:offline | hpagent/hpagent-api:offline |
| `HPAGENT_API_PASSWORD` | 占位值，生产需独立生成 | 必填/开发占位回退，生产不用回退值 |
| `HPAGENT_DOCUMENT_IMAGE` | hpagent/hpagent-document:offline | hpagent/hpagent-document:offline |
| `HPAGENT_ENV` | development | development |
| `HPAGENT_IMAGE` | hpagent/hpagent:offline | hpagent/hpagent:offline |
| `HPAGENT_MIGRATE_IMAGE` | hpagent/hpagent-migrate:offline | hpagent/hpagent-migrate:offline |
| `HPAGENT_MIGRATE_PASSWORD` | 占位值，生产需独立生成 | 必填/开发占位回退，生产不用回退值 |
| `HPAGENT_MODELS_PATH` | /app/config/models.yaml | /app/config/models.yaml |
| `HPAGENT_WORKER_PASSWORD` | 占位值，生产需独立生成 | 必填/开发占位回退，生产不用回退值 |
| `HTTPS_PROXY` | 空（待填） | 空 |
| `HTTP_PROXY` | 空（待填） | 空 |
| `LOG_LEVEL` | INFO | INFO |
| `MINIMAX_API_BASE_URL` | 空（待填） | 未提供缺省 |
| `MINIMAX_API_KEY` | 空（待填） | 未提供缺省 |
| `MINIMAX_FLAGSHIP_MODEL` | 空（待填） | 未提供缺省 |
| `MINIMAX_HIGHSPEED_MODEL` | 空（待填） | 未提供缺省 |
| `MINIMAX_STANDARD_MODEL` | 空（待填） | 未提供缺省 |
| `NAPCAT_ACCOUNT` | 空（待填） | 未提供缺省 |
| `NAPCAT_QUICK_PASSWORD` | 空（待填） | 必填/开发占位回退，生产不用回退值 |
| `NO_PROXY` | localhost,127.0.0.1,::1,searxng,gotenberg,app-postgres,redis,temporal,hindsight,hpagent,hpagent-api | localhost,127.0.0.1,::1；localhost,127.0.0.1,::1,searxng,app-postgres,redis,temporal,hindsight,hpagent,hpagent-api；localhost,127.0.0.1,::1,searxng,app-postgres,redis,temporal,hindsight,hpagent,hpagent-api,hpagent-document-worker；localhost,127.0.0.1,::1,searxng,gotenberg,app-postgres,redis,temporal,hindsight,hpagent,hpagent-api |
| `QQ_BINDING_CHALLENGE_SECONDS` | 300 | 300 |
| `QQ_BINDING_CODE_PEPPER` | 占位值，生产需独立生成 | 必填/开发占位回退，生产不用回退值 |
| `QQ_OFFICIAL_APP_ID` | 空（待填） | 空 |
| `QQ_OFFICIAL_CLIENT_SECRET` | 空（待填） | 必填/开发占位回退，生产不用回退值 |
| `QQ_OFFICIAL_SANDBOX` | false | false |
| `RUN_BUDGET_MODE` | enforce | enforce |
| `SEARXNG_IMAGE` | searxng/searxng@sha256:b36af7984b87191b595bc5301418ed6432c047668a4547ab531a7439b816fac3 | searxng/searxng@sha256:b36af7984b87191b595bc5301418ed6432c047668a4547ab531a7439b816fac3 |
| `SEARXNG_SECRET` | 占位值，生产需独立生成 | 必填/开发占位回退，生产不用回退值 |
| `SEARXNG_URL` | http://searxng:8080 | http://searxng:8080 |
| `SILICONFLOW_API_KEY` | 空（待填） | 未提供缺省 |
| `SILICONFLOW_BASE_URL` | 空（待填） | 未提供缺省 |
| `SILICONFLOW_DeepSeek_R1_0528_Qwen3_8B_MODEL` | 空（待填） | 未提供缺省 |
| `SILICONFLOW_EMBEDDING_MODEL` | 空（待填） | 未提供缺省 |
| `SILICONFLOW_RERANK_MODEL` | 空（待填） | 未提供缺省 |
| `WEB_COOKIE_SECURE` | false | false |
| `WEB_CSRF_SIGNING_KEY` | 占位值，生产需独立生成 | 必填/开发占位回退，生产不用回退值 |
| `WEB_CURSOR_ACTIVE_KEY_ID` | v1 | v1 |
| `WEB_CURSOR_SECRET` | 占位值，生产需独立生成 | 必填/开发占位回退，生产不用回退值 |
| `WEB_FILE_SHELL_ENABLED` | false | false |
| `WEB_FILE_TRANSFORM_ENABLED` | false | false |
| `WEB_FILE_UPLOAD_ENABLED` | true | true |
| `WEB_GATEWAY_IMAGE` | hpagent/web-gateway:offline | hpagent/web-gateway:offline |
| `WEB_GATEWAY_PORT` | 80 | 80 |
| `WEB_PUBLIC_ORIGIN` | http://127.0.0.1:5173 | https://localhost |
| `WEB_SESSION_TOKEN_PEPPER` | 占位值，生产需独立生成 | 必填/开发占位回退，生产不用回退值 |
| `WORKSPACE_ISOLATION_MODE` | single_process_account_lock | single_process_account_lock |

## 空库初始化约定

- 生产建立独立空卷；旧开发/测试数据无需迁移，尚在运行的本地库不作为生产输入。
- 保留 `persistence/docker-init/`、全部 `persistence/migrations/` 与 `hpagent-migrate`。这些文件也是从零创建角色、表、索引和权限的执行链。
- API、Worker 和测试依赖 migration manifest/checksum；本轮不合并或删除 SQL，不改变初始化器。
- 生产环境、域名、Secret、模型和存储确认后，再执行[首次部署](deployment.md)。

## 待确认事项

- E01–O03 中标为待确认的项目尚无用户选定值；模型档位须以真实账户/供应商请求验证。
- 生产 Compose 尚未实现去除源码挂载、容量透传及内部数据库凭据参数化；缺少目标机器与配置决定。
- 当前构建排除规则未完整覆盖 `.env`/本地设计输入，Document 的 `src` context 也需专门审查；生产镜像内容尚未验收。
- 宿主网络变化记录包含 Hindsight 周期重启线索；缺少其稳定运行与真实模型链路的当前证据。
- 真实Worker、外发（若启用）、长期容量释放和匹配的备份恢复尚无首发环境验收记录。
