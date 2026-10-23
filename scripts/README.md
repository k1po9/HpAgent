# 脚本入口

从仓库根目录按下表执行；Shell 入口自行定位仓库，不依赖调用者 cwd。Python 工具使用已安装 `requirements-dev.txt` 的解释器；有源码导入的实验/专项验收加 `PYTHONPATH=src`。先读 `--help`。除内部工具外，参数错误返回非零；执行失败不得视为验收通过。日志、身份、模型、网络和备份输出可能含敏感内容，保存到受限位置。

## 常用入口与副作用

| 入口与典型命令 | 前置条件、参数/环境 | 副作用、输出与适用环境 |
| --- | --- | --- |
| `make help` / `make docs-check` / `make scripts-test` | Python ≥3.11与项目依赖；后两者分别校验当前文档、运行隔离脚本测试 | 只读文档；脚本测试仅用独立文件与本地隔离HTTP服务，不连接业务库/模型。错误非零。 |
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

- `check/release_smoke.py` 是 Shell 发布入口的实现；`check/docs-links.py`、`check/ci-test-paths.py`、`check/temporal_registry.py` 分别负责当前文档链接与命令、CI显式测试路径及四类测试入口覆盖、Worker注册/沙箱门禁。后两者供CI使用，不发真实模型请求。
- `operations/start-hindsight.sh` 是 Compose 挂载的容器启动入口，会启动Hindsight；不要在宿主机手动执行。
- `benchmarks/outbox/outbox_benchmark_worker.py` 是受实验驱动的子进程；`agent_strategy_benchmark.py`为runner，`summarize_agent_strategy_benchmark.py`为汇总器，`agent_strategy_tasks.yaml`为输入。它们不是发布脚本，命令和统计口径见[Benchmark索引](benchmarks/README.md)。
- `web/scripts/e2e-frontend.mjs` 统一源码服务、独立构建预览与独立生产构建；不复用已有前端服务。
- `web/scripts/e2e-backend.sh` 是Playwright内部API启动器；其迁移、账户初始化和Fake配置只适用于显式隔离环境。
- `web/scripts/*.mjs` 是专项浏览器工具，可能要求特定路由、HTML和隔离夹具，不能作为当前通用门禁。浏览器路径可覆盖，`--help`不启动浏览器；实际运行要求`HPAGENT_REVIEW_ISOLATED=1`和`--isolated`。保留其中尚未证明等价的断言，运行前核对脚本输入；默认验证使用上表正式入口。

当前开发/测试环境见[开发指南](../docs/development/setup.md)和[测试指南](../docs/development/testing.md)。发布、日志、备份恢复分别以现有operations文档为准，不在脚本中维护第二套部署流程。

## Web 检索供应商对照工具

`PYTHONPATH=src python scripts/experiments/web_retrieval_compare.py --execute --output /absolute/new/result-dir`

对照 legacy/DeepSeek Search 和固定官方 URL 的 Local/DeepSeek Fetch，不访问业务库或模型，不启动服务。`--searxng-url` 指定搜索入口，默认 `http://127.0.0.1:8085`；结果写仓库外新目录。模块设计见[Web Retriever v1](../docs/architecture/capabilities.md#web-retriever-v1-设计总结)。

**会产生检索费用**：每轮最多 9 次 DeepSeek 尝试，无自动重试；仅使用既有 `DEEPSEEK_API_SEARCH_FETCH`。脚本内 `LEDGER` 指定的累计账本必须已存在且有效，每次尝试发送前加锁落盘，失败/未知也永久占额，累计上限 1,000；不得删除、重置或替换历史来恢复名额。401/402/403 停止后续 DeepSeek 请求。实验账本不替代产品 Run/Work 预算；脚本完成不代表 Research 报告通过。
