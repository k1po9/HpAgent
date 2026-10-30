# 脚本入口

从仓库根目录按下表执行；Shell 入口自行定位仓库，不依赖调用者 cwd。Python 工具使用已安装 `requirements-dev.txt` 的解释器；有源码导入的实验/专项验收加 `PYTHONPATH=src`。先读 `--help`。除内部工具外，参数错误返回非零；执行失败不得视为验收通过。日志、身份、模型、网络和备份输出可能含敏感内容，保存到受限位置。

## 常用入口与副作用

| 入口与典型命令 | 前置条件、参数/环境 | 副作用、输出与适用环境 |
| --- | --- | --- |
| `make help` / `make docs-check` / `make scripts-test` | Python ≥3.11与项目依赖；后两者分别校验当前文档、运行隔离脚本测试 | 只读文档；脚本测试仅用独立文件与本地隔离HTTP服务，不连接业务库/模型。错误非零。 |
| `make architecture-check` | Python ≥3.11、requirements-dev；`PYTHON` / `PYTEST_ARGS` 可覆盖 | 扩展现有 temporal_registry 检查与接入契约测试；只组装惰性协作者，不连接服务、不读私有 `.env`、不调用 Provider。JSON 报告来源/配置/能力/Worker/sandbox 边界，随后 pytest；失败非零，已知 strict xfail 见[测试指南](../docs/development/testing.md)。 |
| `make ci-web` | Node ≥22、`make web-install`（npm ci） | lint/typecheck/unit/build；默认构建写 `web/dist`，运行预览占用它时应指定其他 outDir，见测试指南。 |
| `make test-unit` / `make test-db` / `make test-api` / `make test-temporal` | Python ≥3.11与开发依赖；基础设施入口按选中用例要求显式专用DSN、Temporal及Redis | 本地与CI使用相同选择；PG包含Work，Temporal按标记覆盖全部目录。缺配置在fixture前失败。测试范围与副作用见[测试指南](../docs/development/testing.md)。 |
| `make e2e` / `make e2e-canonical` | 三角色专用同库DSN、`REDIS_URL`、`FILE_STORE_ROOT`、`E2E_ISOLATED=1`；`WEB_API_PORT=8180`、`WEB_DEV_PORT=5273` 可覆盖 | 启动自己的API/Vite、迁移测试库、注册alice/bob并写业务夹具，启用Fake执行器；不用真实模型。输出 `web/test-results`。仅隔离测试环境；已有服务不会被复用。canonical选择正式源用例并构建独立生产产物。 |
| `cd web && npm run test:e2e:fixtures` | Playwright Chromium；`HPAGENT_FIXTURE_PORT=5312` 可覆盖 | 组件 harness / 路由夹具；仅启动独立前端，无后端。输出 `web/test-results/fixtures`。 |
| `cd web && npm run test:e2e:visual` | `HPAGENT_VISUAL_ISOLATED=1`、显式 `HPAGENT_API_TARGET`、独立 API 及账户；`HPAGENT_VISUAL_PORT=5290` 可覆盖 | 启动自己的前端，访问指定测试 API；截图写 `web/test-results/evidence/visual`。 |
| `make e2e-contract` | 先构建，可用 `HPAGENT_E2E_BUILD_DIR` 指定目录；Playwright Chromium；可用 `HPAGENT_ENTRY_BASE_URL` 指向独立静态服务、`HPAGENT_BROWSER_EXECUTABLE` 指定浏览器 | 浏览器拦截API/Auth请求，验证真实构建产物；默认只启动本地5311预览。不迁移数据库、不调用模型；输出 `web/test-results/contract`。 |
| `bash scripts/operations/logs.sh --api --tail 100 --no-follow` | Docker Compose；`--worker/--infra/--qq` 或服务名 | 读取当前容器日志，默认持续跟随；不改业务数据，stdout可能含敏感信息。 |
| `BACKUP_DIR=/secure/hpagent bash scripts/operations/backup.sh` | Docker Compose、python3、tar；或 `--output` 指定仓库外绝对目录 | 读取App/Hindsight PG与`.data/workspace`；创建权限受限独立子目录。失败保留`.incomplete`并非零退出。不含Temporal/File Store，不保证跨存储一致性。人工运维。 |
| `python scripts/operations/bootstrap-identity.py --help` | 管理员`MIGRATION_DATABASE_URL`；`--web-subject/--qq-channel/--qq-subject` | 写入已核实身份绑定；可能新建owner账户，不创建登录密码，不代替QQ所有权验证。不合并冲突账户；摘要stdout，冲突非零。 |
| `python scripts/operations/create-registration-invite.py --help` | 管理员DSN；`--profile`、额度、有效期与赎回次数 | 写邀请码与权益配置；stdout含一次性邀请码，需私密保管。人工管理，非法参数/失败非零。 |
| `python scripts/operations/observability-viewer.py --help` | 日志目录、只读DSN、监听地址/端口以帮助为准 | 启动只读本地HTTP诊断页，读取日志与PG；不修改业务。不要公开到公网。 |
| `python scripts/operations/admin-console.py --help` | 本机 `.env` 的迁移/API/Worker/Temporal/Hindsight 密码；Docker Compose；默认 `127.0.0.1:8092`，host 仅限 `127.0.0.1/localhost` | 按需打开本地管理页：新建邀请码、查看用户、拉取 Docker 日志、执行 SQL。启动随机请求令牌由首页传给 API，校验 Host/Origin/浏览器来源并禁止嵌入，信任本机操作者。应用库支持多条 SQL 并逐条展示状态/结果，每组最多显示 500 行（非数据库读取上限，大查询自行加 LIMIT）；默认整批提交、错误回滚，显式 COMMIT 会提前提交。写操作连接中断先核对状态再重试。不常驻，不进入产品 Web。页面可显示一次性邀请码。不要通过代理公开到公网。 |
| `bash scripts/operations/docker-offline-export.sh /outside/images.tar web-prod` | Docker Compose；tar路径、一个或多个profile；默认`dist/hpagent-offline-images.tar` | 构建/拉取并导出镜像，写tar、manifest、SHA256；拒绝覆盖已有tar，不启动应用。制品应转存仓库外。 |
| `bash scripts/operations/docker-offline-load.sh /outside/images.tar web-prod` | Docker Compose；校验文件存在时需sha256sum | 校验并加载本机Docker镜像，再核对所需镜像；不启动应用，缺镜像非零。 |
| `bash scripts/check/release-smoke.sh --help` | 仅`web-prod`；`--execute`显式执行，`SMOKE_USER/SMOKE_PASSWORD`为已有专用账户；`WEB_GATEWAY_URL`或默认采用Compose API的`WEB_PUBLIC_ORIGIN`（两者必须一致），`--timeout/--request-timeout` | **可能build/up/迁移，登录、创建会话、发送真实模型请求**。核对所有服务、迁移退出、API/Worker健康及本次Run的成功消息。`--no-start`复用部署仍写业务；`--skip-build`仅跳过构建。不自动停止或删除记录；脱敏摘要stdout，失败非零。人工发布环境；不在日常测试直接运行。 |
| `bash scripts/check/gateway-smoke.sh` | Docker、curl、python3；`GATEWAY_IMAGE/GATEWAY_PORT/STUB_IMAGE` | 可构建前端/镜像，创建独立stub/网关容器及私有网络；宿主仅发布网关随机端口。结束仅删除本次资源。不连接真实API/模型；stdout结果，失败非零。CI网关检查。 |
| `bash scripts/check/schema-runtime-smoke.sh` | Docker Compose、python3；无参数 | 构建Migration/API/Worker镜像并运行隔离检查容器，只读核对镜像内SQL清单/哈希；不执行迁移。stdout指纹，不一致非零。 |
| `psql "$APP_DATABASE_URL" -v ON_ERROR_STOP=1 -v run_id=UUID -f scripts/check/queued-run-diagnostic.sql` | psql、目标库只读权限 | 只读Run/Outbox/Workflow分发状态，stdout诊断行；不重试或解锁业务。SQL是内部查询，无CLI。 |
| `python3 scripts/check/model-map.py --env-file .env --beta` | Docker Compose、PyYAML；指定私有.env，--beta合并Beta overlay | 仅解析配置并输出脱敏用途/模型/凭据存在状态；不启动容器、不调用模型。错误不回显原始配置。详见[模型分工](../docs/reference/model-routing.md)。 |
| `python scripts/check/models.py --help` / `bash scripts/check/models-compose.sh --help` | 模型配置与Provider凭据；后者需Docker，`--build`会构建，`--direct/--stream/-n/-c`见帮助 | **实际请求模型并产生费用**；stdout延迟/错误。只能对获准Provider运行，非法次数/类别/模式在读取凭据前失败；任一样本失败则非零退出，统计口径不变，不是业务链验收。 |
| `python scripts/check/mcp-health.py --help` | MCP配置、依赖；`--server/--timeout/--json/--strict-security` | 连接外部MCP并查询工具元数据，不执行工具调用；stdout报告，安全/连接失败依选项非零。 |
| `python scripts/check/qq-connectivity.py --help` | QQ官方凭据；沙箱开关`QQ_OFFICIAL_SANDBOX` | **请求官方token/gateway并等待WebSocket事件**；不作为离线测试。不输出/公开诊断中的凭据；连接失败非零。 |
| `bash scripts/dev/mcp.sh --help` | 项目venv与本地MCP依赖；`start/--status/--stop` | 启停本地服务、写`.data/mcp.pids`；只有status只读。开发辅助，不用于生产管理。 |
| `bash scripts/dev/reset.sh --help` | Docker/Temporal/本地进程权限；选项见帮助 | **可终止进程/Workflow或删除本地数据**；无参数也会执行重置。仅用于可丢弃开发环境。 |
| `make agent-benchmark-check` / `make agent-benchmark-run` | 项目Python依赖、独立实验环境；`PYTHON`可覆盖；`BENCHMARK_OUTPUT_DIR`可覆盖结果路径 | check探测环境，run调用真实模型并写实验记录。结果默认`artifacts/benchmark-runs/agent_strategy`（忽略）；运行配置还用`.data/benchmarks`。实验口径和输入见下方索引。 |
| `PYTHONPATH=src python scripts/benchmarks/temporal/temporal_recovery_benchmark.py --help` | 专用Temporal namespace、Worker进程harness；参数见帮助 | SIGKILL实验子进程，写SQLite/标记/逐条与汇总；默认`artifacts/benchmark-runs/temporal/workflow_worker`。不应接入业务namespace。 |
| `PYTHONPATH=src python scripts/benchmarks/temporal/activity_worker_recovery_benchmark.py --help` | 同上，Activity故障边界 | 默认`artifacts/benchmark-runs/temporal/activity_worker`；只对专用实验环境运行，非通过结果仍保留。 |
| `PYTHONPATH=src python scripts/benchmarks/outbox/outbox_recovery_benchmark.py --help` | 专用PG/Temporal、实验库名/namespace参数 | 创建/写实验库并杀实验Worker；结果默认`artifacts/benchmark-runs/outbox`。不得使用业务库。 |
| `python3 scripts/acceptance/s7_1.py --help` | S7-1 已授权范围、Docker 及本地依赖镜像；`--output` 必须为仓库外新建绝对目录 | freeze/preflight 离线；MVP source-precheck 仅专用搜索/出口和产品来源 Adapter，不读模型凭据、不付费，检查正文及概念覆盖；prepare 精确读取获准的三个模型变量，创建本批专用 PG/Redis/Temporal/Worker/API/前端和受限出口；run 显式开启真实模型与故障允许列表，产生费用；verify 只读，cleanup 仅删除本批标签资源。保留跨批预算账本及证据，详见[测试指南](../docs/development/testing.md)。 |
| `PYTHONPATH=src python scripts/acceptance/workspace_scale.py --help` | 显式三角色DSN、`FILE_STORE_ROOT/RUN_SCOPE_ROOT`；依赖以requirements为准 | 大规模写入PG与File Store，输出发现/物化规模指标；仅一次性专项隔离验收，不用于共享数据。 |

## 内部辅助与维护位置

`PYTHONPATH=src python scripts/operations/memory-bank-initialize.py --senders-stopped
--config config/hindsight.pgroonga.yaml` 用显式 `WORKER_DATABASE_URL` / `HINDSIGHT_URL`
在空目标中初始化当前活跃账户的受控 Bank。它先后检查空库和本地队列；重复运行验证同一配置，
已有配置不一致或有语义数据时拒绝，不覆盖 Bank。停写参数只是操作者声明，需另核实进程。
该入口只调用 Bank 管理 API，不调用模型。

`psql -X -v ON_ERROR_STOP=1 -f scripts/operations/memory-cutover-inventory.sql`
供停止 Hindsight API 后在显式活动记忆库中做只读空库检查。它拒绝任一语义/队列表非空、
未完成请求或缺表；允许 Bank 配置和已完成请求日志。它不检查应用库，也不证明停写。

`memory-cutover-digest.sql` 使用显式 `-v schema_name=hpagent` 或 `public` 在停写库中
输出各表完整行计数/SHA256。数据正文只在数据库内聚合；用于
成对当前快照恢复前后比较，不能以热态摘要代替冻结点，也不检查schema、表外角色权限或文件字节。

`docker-compose.pgroonga.yaml` 是与 base + beta 显式组合的准备补丁：新 PG/API 使用独立卷，
Worker Recall 关闭；`docker-compose.pgroonga.enabled.yaml` 仅在切换检查后开启 Recall。
新 API 在维护阶段不自动重启，以免重复启动探测；启用补丁恢复正常重启策略。
`docker-compose.pgroonga.recovery.yaml` 指向另一新卷，供停写后恢复当前成对快照。
这些文件不会自行切换部署，不能单独 `up` 或以旧 native 快照覆盖已有增量。

`PYTHONPATH=src python scripts/operations/memory-cutover-check.py --require-empty` 只读检查显式
`WORKER_DATABASE_URL`、`HINDSIGHT_URL`（可选 `HINDSIGHT_API_KEY`）及
`HPAGENT_MIGRATIONS_DIR` 指向的目标。核对完整应用 checksum、1.1.0 服务、所有 Bank 的文档、
待对账 Operation、两表状态和记忆 Outbox；无正文 JSON 输出。返回 0 表示数据检查通过，2 表示
存在待处理数据/任务，1 表示检查失败；缺参数由 argparse 返回 2。去掉 `--require-empty` 可检查
新链路运行后的来源/范围与进度。未登记来源报告 isolated，依赖应用读取白名单拒绝，脚本不改远端 tags。
不停止进程、不清空、不迁移、不推进队列、不开放 Recall；不能证明旧发送者已停发或同步请求已结束。
实际切换见[部署](../docs/operations/deployment.md#记忆核心空库启用)。

同一入口的 `--service-only --service-container CONTAINER --memory-db-host HOST
--memory-db-name DATABASE [--memory-db-port 5432] [--memory-db-schema public] [--config PATH]`
独立核对关键词后端。该模式需要运维 Docker 权限，读取指定容器 API 进程的白名单字段，
在容器内按其实际连接运行限时只读事务；不向 Chat/Worker 提供 Docker 或记忆库管理权限。
数据库身份必须显式给出，不能从容器默认库推断。输出包含本地期望、进程有效后端、DB/schema、
PG16/扩展版本、完整索引表达式和参数、有效性、Bank 开关及待协调操作计数，不含 DSN/正文。
0 为该关键词检查通过，2 为拒绝，1 为检查失败；transport/权限/目标/列/索引/Bank 等有不同错误码。
它仅证明所检查的关键词状态，不证明服务模型或中文质量。省略 `--service-only` 可同时执行原
本地来源/Outbox 数据检查，此时必须给原有显式 DSN/URL；API URL 必须直接对应同 Docker 主机的
容器地址或发布端口，无法关联的反向代理地址拒绝组合证明，可分别检查两项。省略所有服务参数
仍是原有 data-only 检查，不证明后端。两项都不停止发送者、不改变 Recall 开关或部署路由。

- `check/release_smoke.py` 是 Shell 发布入口的实现；`check/docs-links.py`、`check/ci-test-paths.py`、`check/temporal_registry.py` 分别负责当前文档链接与命令、CI显式测试路径、架构门禁及四类测试入口覆盖、Worker注册/沙箱门禁。后两者供CI使用，不发真实模型请求；temporal_registry 也由 `make architecture-check` 统一暴露，检查实际队列/重复注册与全部捕获 Workflow 的 sandbox。
- `operations/hindsight-entrypoint.py` 将质量 YAML 的服务模型配置展开到子进程环境，再 exec Compose 挂载的 `operations/start-hindsight.sh` 启动 Hindsight；不要在宿主机手动执行。
- `benchmarks/outbox/outbox_benchmark_worker.py` 是受实验驱动的子进程；`agent_strategy_benchmark.py`为runner，`summarize_agent_strategy_benchmark.py`为汇总器，`agent_strategy_tasks.yaml`为输入。它们不是发布脚本，命令和统计口径见[Benchmark索引](benchmarks/README.md)。
- `web/scripts/e2e-frontend.mjs` 统一源码服务、独立构建预览与独立生产构建；不复用已有前端服务。
- `web/scripts/e2e-backend.sh` 是Playwright内部API启动器；其迁移、账户初始化和Fake配置只适用于显式隔离环境。
- `web/scripts/*.mjs` 是专项浏览器工具，可能要求特定路由、HTML和隔离夹具，不能作为当前通用门禁。浏览器路径可覆盖，`--help`不启动浏览器；实际运行要求`HPAGENT_REVIEW_ISOLATED=1`和`--isolated`。保留其中尚未证明等价的断言，运行前核对脚本输入；默认验证使用上表正式入口。

当前开发/测试环境见[开发指南](../docs/development/setup.md)和[测试指南](../docs/development/testing.md)。发布、日志、备份恢复分别以现有operations文档为准，不在脚本中维护第二套部署流程。

## Web 检索供应商对照工具

`PYTHONPATH=src python scripts/experiments/web_retrieval_compare.py --execute --output /absolute/new/result-dir`

对照 legacy/DeepSeek Search 和固定官方 URL 的 Local/DeepSeek Fetch，不访问业务库或模型，不启动服务。`--searxng-url` 指定搜索入口，默认 `http://127.0.0.1:8085`；结果写仓库外新目录。模块设计见[Web Retriever v1](../docs/architecture/capabilities.md#web-retriever-v1-设计总结)。

**会产生检索费用**：每轮最多 9 次 DeepSeek 尝试，无自动重试；仅使用既有 `DEEPSEEK_API_SEARCH_FETCH`。脚本内 `LEDGER` 指定的累计账本必须已存在且有效，每次尝试发送前加锁落盘，失败/未知也永久占额，累计上限 1,000；不得删除、重置或替换历史来恢复名额。401/402/403 停止后续 DeepSeek 请求。实验账本不替代产品 Run/Work 预算；脚本完成不代表 Research 报告通过。


## 记忆质量配置

```bash
PYTHONPATH=src python scripts/operations/memory-config.py validate
PYTHONPATH=src python scripts/operations/memory-config.py plan
PYTHONPATH=src python scripts/operations/memory-config.py inspect --account ACCOUNT_UUID
PYTHONPATH=src python scripts/operations/memory-config.py apply --senders-stopped --backup /secure/new-memory-profile-backup.json
PYTHONPATH=src python scripts/operations/memory-config.py restore --senders-stopped --backup /secure/new-memory-profile-backup.json
```

需要应用 Python 依赖。validate 只读本地 hindsight.yaml、prompts/memory.yaml，不需要服务/凭据，不做
模型请求；可用 --config/--prompts 选文件。其他命令要求显式 WORKER_DATABASE_URL、HINDSIGHT_URL，
可选 HINDSIGHT_API_KEY，以及完整迁移链的 HPAGENT_MIGRATIONS_DIR。账户从该业务库可信 active
账户映射；重复 --account 可限定账户，缺省检查全部此类账户。不存在的 Bank 只报告，后续首次
合法 Retain 初始化，不因 inspect/plan 创建。输出只含参数名、计数、标量和文本 hash，不含
mission 原文、Key、记忆正文或 Provider 错误正文。退出 0 表示命令成功，2 表示失败；plan/inspect
展示 matches 和 strategy_conflicts，命令成功不表示全部配置相同。

validate 的 profile_hash 保持受控 Bank 参数口径，config_hash/service_hash 和 text_search
另行报告包含关键词后端的本地冻结身份；它们不表示服务或模型已验证。

apply/restore 写远端 Bank；必须先按[切换流程](../docs/operations/deployment.md#记忆质量配置切换)
停所有记忆发送者，--senders-stopped 是操作员声明，不是自动停发证明。脚本再校验所选账户本地
动作无在途状态、远端 pending/processing/failed Operation 与 pending/failed Consolidation 已排空。
仅 apply 可修复 send_state=idle、尚未取得发送资格、冻结 profile 与期望完全一致的 queued/uncertain
Retain；这不允许切换其配置。此时运维环境须与 Worker 使用相同的服务模型目录变量以核对 hash。
历史无快照、sending/unknown 或不同 profile 仍拒绝；--senders-stopped 声明仍然必需。
存在未接管的 Retain/Consolidation Strategy 时拒绝写，避免其他策略悄悄覆盖期望字段。
apply 的 --backup 必须为尚不存在的文件且父目录已存在，以 0600 保存原受控 override；路径放
仓库外或受限忽略目录。已有文件不覆盖。restore 按该文件逐项恢复，原无 override 的字段发 null，
不 reset 整个 Bank；不依赖当前质量文件仍有效。备份含策略文本，按受限配置文件保存。
多 Bank 写入不是跨 Bank 事务；中途失败先 inspect 对账，再决定继续或 restore。
脚本不修改本地动作、部署、Provider 环境或 Recall 门控，也不迁移历史事实。


## PGroonga 离线对照

`python3 scripts/experiments/hindsight_pgroonga.py --help` 提供隔离 `collect` 与离线
`summarize`。输入为 `test/fixtures/hindsight-pgroonga/evaluation/dataset.json` 的固定合成
文档、Observation 来源和 gold；`seed.py` 在已有双后端夹具中按独立 session UUID 直接载入
同一事实/向量，重复 session 拒绝，不通过两次 Retain 生成不同事实。`plans.py` 使用当前服务
SQL builder 在自有 schema 检查 3/300/3000 条 Bank 的两表计划；不关闭 seqscan。

先冻结协议（数据 hash、session、固定参数、阈值、重复顺序与资源上限），再在各自 internal
网络的应用开发 runner 中执行 `collect --url http://hindsight:8888 --session SESSION
--arm native --batch 0 --protocol /absolute/protocol.json`，candidate 和三轮分别运行，
从仓库根目录读取输入。stdout 是白名单 JSONL，完整 trace 只在进程内；stderr 不应当作安全
trace 导出。`summarize --results /absolute/results.jsonl --output /absolute/metrics.json`
仅计算已有结果。结果、协议、命令与资源采样放忽略目录，不能覆盖失败批次。

该工具运行真实 SDK/Adapter/ContextAssembly，但本地 readable 状态和上下文绑定使用夹具；
不证明真实应用 PG 权限、lease/fence 或持久化。模型为 mock、384 维常量向量、原生 rrf，
固定 original 查询替代未调用的 HyDE；只证明工程契约，不能据此判断真实中文语义质量。
最终指标先截断到 k 个记忆结果，再按完整 Observation 来源计入 gold 文档；关键词指标
单列 world 臂，避免合并不同 fact_type 的局部排名。标识符组的 Recall 即精确术语命中率。
首次访问不等于冷缓存，三次重复不扩大独立质量样本数。无答案误注入表示上下文被选中，
没有调用回答模型，因此不报告模型拒答率。真实 Provider 对照须另有明确模型、端点、
数据范围及请求/费用上限；本工具没有真实 Provider 调用模式。


PGroonga 治理恢复的合成辅助入口是
`test/fixtures/hindsight-pgroonga/recovery_app.py --help`、`recovery_db.py` 与
`recovery.compose.yaml`，实际资源和验证参数见[测试指南](../docs/development/testing.md#hindsight-版本与隔离契约)。
这些辅助程序只接受显式隔离数据库/服务，不是业务迁移或切换工具；原始备份必须在仓库外受限位置。
有切换后写入时，用已停止发送者的当前应用/记忆完整快照恢复，不丢弃纠错、删除和撤权增量。


QQ / OneBot11：`operations/snowluma-config.py --bot-id ...` 在 bot 停止时初始化新目录和
secret（默认 UID/GID 1000），`--check` 只回读有效 snapshot；冲突/多账号/符号链接拒绝。
`check/snowluma-version.py` 在 network=none 的临时容器检查固定 digest 内 SnowLuma/Node/QQ，
不启动登录；`check/onebot-status.py --path ...` 读取 Worker 脱敏 QQ readiness，不发送动作。
具体启用、扫码与持久投递恢复见[部署](../docs/operations/deployment.md)。
