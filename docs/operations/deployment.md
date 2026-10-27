# 首次部署

首次上线使用独立空库；先在[配置确认表](first-release-configuration.md)逐项确定域名、凭据、模型、功能与存储，再准备生产环境。

## Compose 拓扑

核心基础设施服务：`app-postgres`、`redis`、`temporal-postgres`、`temporal`、`hindsight-postgres`、`hindsight`、`searxng` 和 `gotenberg`。

应用服务：

- `hpagent-migrate`：一次性应用 Schema Migration。
- `hpagent-api`：FastAPI 命令接收、查询、认证、文件、Work / Research / Artifact、通知查询与 SSE。
- `hpagent`：主 Orchestration Worker 和已启用的 QQ Ingress。
- `hpagent-document-worker`：独立 Heavy Document Activity Worker。
- `web-dev`：Vite 开发前端。
- `web-gateway`：生产 Nginx 前端/API Gateway。

Profile：

- `web`：API、Worker、Migration、依赖和 Vite。
- `web-prod`：API、Worker、Migration、依赖和生产 Gateway。
- `agent`：不含 Web API/前端的 Worker、Migration 和依赖。
- `qq`：可选 SnowLuma 服务及依赖。
- `tools`：可选 Temporal UI 及依赖。

## 启动

```bash
cp -n .env.example .env
# 先按配置入口补齐全部凭据、预算、绑定地址和SnowLuma版本
export COMPOSE_PROJECT_NAME=hpagent-beta
export COMPOSE_FILE=docker-compose.yaml:docker-compose.beta.yaml
docker compose --profile web-prod up -d --build
docker compose --profile web-prod ps
curl --fail http://127.0.0.1:8080/health/ready
curl --fail http://127.0.0.1:${WEB_GATEWAY_PORT:-80}/
```

对外暴露前需要配置生产 Secret、Provider Credential、`WEB_PUBLIC_ORIGIN`，并按部署环境设置 `WEB_COOKIE_SECURE=true`。Compose 将 PostgreSQL、Redis、Temporal、SearXNG、Gotenberg、Hindsight 和 API 绑定在 Loopback；Gateway 默认也绑定 Loopback，Beta 需按已核实Tailscale拓扑指定绑定地址及防火墙。

离线镜像传输使用 `scripts/operations/docker-offline-export.sh` 和 `scripts/operations/docker-offline-load.sh`。

Compose 把 `scripts/operations/start-hindsight.sh` 挂载为 Hindsight 启动入口，并配置专用的 `hindsight-postgres` 与 `HINDSIGHT_API_DATABASE_URL`；当前部署不使用 embedded pg0。

### Beta PGroonga 运行组合

采用 PGroonga 的 Beta 使用以下完整组合，应用治理 API 与 Worker 均连接
`http://hindsight-pgroonga:8888`，记忆数据库为
`hindsight-pgroonga-postgres:5432/hindsight_110_pgroonga`。旧 native API 保持停止，旧卷保留。

```bash
export COMPOSE_PROJECT_NAME=hpagent-beta
export COMPOSE_FILE=docker-compose.yaml:docker-compose.beta.yaml:docker-compose.pgroonga.yaml:docker-compose.pgroonga.enabled.yaml
docker compose --env-file .env --profile web-prod ps -a
```

更新应用时显式指定 `hpagent hpagent-api hpagent-document-worker web-gateway`，
使用 `--no-deps`，先核对版本与配置，不执行无服务列表的全项目 `up`：旧 native 服务仍在组合中，
全项目启动会重新唤醒旧写入目标。只使用 base + beta 会把应用 URL 切回 native。
准备组合不含 enabled 时 Recall 关闭、目标 API 不自动重启；完成观察后才恢复正常重启策略。
正常组合不包含 recovery；只有停写、成对当前快照恢复到独立新卷时才加入 recovery 补丁。
新 API 不发布宿主端口，旧 loopback 8001/9999 不代表新目标健康。
冷备须使用完整组合，归档包含这三个 PGroonga 补丁；私有 `.env` 仍单独安全保管。

## 空库初始化

本项目首次上线，不保留旧开发/测试业务数据，不执行旧库转换或历史数据回填。生产使用独立的空数据库/卷，不能把当前日常或测试卷当作生产输入。

`persistence/docker-init/` 创建运行角色，`hpagent-migrate` 执行 `persistence/migrations/` 的完整 SQL 链，创建当前表、索引和权限。文件名虽然叫 Migration，也是从零建库的必要输入；API/Worker 启动时还会校验 manifest/checksum，因此保留初始化器和全部 SQL。

先确认初始化服务成功退出，再检查 API ready、主 Worker healthy 和 Document poller；之后创建首个账户并执行真实模型流程。无需导入旧业务备份；投产后的数据保护见[备份恢复](backup-restore.md)。

## 发布与健康验证

### 记忆核心空库启用

当前交付采用空目标启用，不迁移旧 Hindsight 数据、不重放全部聊天。既有数据不影响隔离验收，
开发验证不需要清除它。以下步骤仅在操作员已明确选定目标和获准切换后执行；代码检查通过不等于已切换。

1. 固定同版本 API/Worker、SDK 1.1.0 和已锁定 Hindsight 镜像 digest。设置
   `HINDSIGHT_RECALL_ENABLED=false`；指定新的 Hindsight PG 卷/地址及配套应用库，
   不复用业务服务进行验收，不混用旧治理两表和新空 Hindsight。使用已有完整 001 起始 SQL 链和 checksum
   初始化应用库至 078（`make migrate`，显式 `MIGRATION_DATABASE_URL`）；在加载配套 API/Worker 前应用完整链，不单独执行 078，也不修改/合并历史 SQL。
   Hindsight 使用目标镜像自带的完整 Alembic 链启动，禁止直接更新其内部表。
2. 先从入口停止接收新 Chat，盘点所有持有旧 Hindsight 地址/凭据的 Worker（包括其他 project、
   宿主进程和在途 Activity）。在批准的维护窗口停止旧发送者；例如在核实的部署目录执行
   `docker compose --profile web-prod stop hpagent`，再用
   `docker compose --profile web-prod ps -a hpagent` 核对进程终止。不能将这个命令当作对所有主机发送者的证明。
   同时核查 API/Document Worker 是否与旧发送链有共享执行者。不得让缺少发送资格检查的旧 Worker 与新链路混跑。
3. 等待并核实旧同步请求在旧服务端结束；原同步写入没有 UUID Operation，新增两表为空不能证明没有旧在途请求。
   旧地址、凭据或发送者无法收敛时不开放 Recall；使用独立的新目标并阻断旧发送者对它的访问，
   不删除未知请求的去重记录，不以远端 404 推断未发送。旧数据舍弃也不能省略发送者核查。
4. 新链路尚未发送时，执行下面的只读命令。要求返回 0 且 `data_ready=true`；输出只含计数和问题类型，
   不能用退出码证明上一步进程/请求已结束。

   ```bash
   # 三项环境变量必须显式指向已选定目标；不从运行容器导出完整环境。
   export HPAGENT_MIGRATIONS_DIR="$PWD/persistence/migrations"
   PYTHONPATH=src python scripts/operations/memory-cutover-check.py --require-empty
   ```

   `WORKER_DATABASE_URL` 使用应用 Worker 最小角色，`HINDSIGHT_URL` 指向同批新目标。
   未登记历史文档会被应用来源白名单隔离，脚本仍返回未就绪；本空目标路线不自动映射为 private，
   不自动删除，也不自动为它创建伪 Run。选择新的空目标，或另行明确处置现有数据。
5. 保持 Recall 关闭，启动同版本新链路，完成测试账户的私聊/群 A/群 B 写入；确认 Chat succeeded，
   来源 action 经 submitted/verifying 到 verified，原 operation_id 固定。再运行不带 `--require-empty`
   的同一命令核对两表/Outbox、所有 Bank 的待对账 Operation、Document scope 和缺失来源。
   pending/processing 继续等待，failed/uncertain 保留阻断并对账，不以 202 或暂时 Recall 空结果认定完成。
   执行来源查看、事实纠正/失效/恢复、定向失效及来源删除 API；重复原幂等命令得到同一 action。
6. 数据检查与外部停发核查都通过后，显式设置 `HINDSIGHT_RECALL_ENABLED=true`，仅在已批准的目标
   recreate 新 Worker，使其读取新环境。以新 Run 验证私聊/群聊隔离及来源验证后再开放入口。
   旧 transcript 的撤回仍按 manifest/scopes 检查，不绕过 current grant、generation 或 fence。
   若失败，先关闭 Recall 并保留来源、动作、Operation 身份供对账；不回退旧版接口或自动清空业务卷。

实际停发、目标清空/卷替换、recreate 和开放入口均是部署操作；运行本仓库单元/夹具不执行这些步骤。
真实 Provider 提取/归纳质量与实际 QQ 外发另行验收；Work 长期记忆和完整 UI 不属于本核心交付。

更新到包含通用文字交付的版本时，保留已有私有 `.env`，按[配置参考](../reference/configuration.md#web-retriever-与-research)补齐检索供应商和独立 Key；不要用模板覆盖原凭据。API 与 Worker 使用同一版本的完整迁移链（包括 071 的多片段 Evidence 约束）和 File Store，先运行 migration，再更新应用。API/Worker 的版本校验与生产者契约必须一致，避免只更新其中一个服务；普通文字发布本身不新增数据库表。

下列命令只供操作员在明确选定的目标目录执行，不代表当前开发目录已部署。后端独立验收通过后，目标目录仍需验证浏览器提交、离页、返回、inbox 和成果下载，见[测试指南](../development/testing.md#真实浏览器全链路)。

宿主机源码挂载变化需要对应 API / Worker 重载；镜像或 environment 变化使用 Compose recreate / build，`restart` 不读取新的容器环境。

```bash
docker compose --profile web-prod up -d --build hpagent hpagent-document-worker hpagent-api web-gateway
docker compose --profile web-prod ps
curl --fail http://127.0.0.1:8080/health/ready
docker compose exec -T hpagent python -m orchestration.worker_health
```

API ready 不能证明 Worker 已校验 Workflow 或能执行 Run。连接模型服务、真实 QQ 渠道和生产默认 Research lease 的恢复验证也分别进行；健康端点通过不替代这些验收。前端操作见[功能指南](web-workbench.md)。

## 发布验证入口与限制

`bash scripts/check/release-smoke.sh --help` 列出完整副作用；实际执行需 `--execute`、已有专用 `SMOKE_USER/SMOKE_PASSWORD`，默认仅使用 `web-prod`。配置必须在API/主Worker设置`HPAGENT_ENV=production`并关闭Fake；网关地址默认采用Compose API的`WEB_PUBLIC_ORIGIN`，显式`WEB_GATEWAY_URL`也必须与其一致，TLS终止和安全Cookie须按实际入口配置。该脚本可能build/up/迁移、写会话并调用真实模型，不属于安全静态检查。

它通过Compose模型逐项核对服务，通过容器健康与迁移退出确认就绪，再验证登录303、CSRF Origin、消息202、本次Run的`succeeded`及其已完成助手消息。Document Worker无独立healthcheck时只证明进程运行，poller和实际文档任务仍须独立验收。网关不代理`/health/ready`，API ready由API容器健康检查验证；不能把网关SPA的200当作API健康。

`--no-start`用于已部署组合，仍有写入和真实请求；脚本不停止服务或删除测试记录。不能用不同Compose project名推断端口、宿主源码或数据卷已经隔离。当前配置仍挂载宿主源码，升级前需固定checkout与镜像版本，并按[备份恢复](backup-restore.md)取得匹配快照；回滚须核对代码/schema兼容，迁移没有自动down路径。


## 记忆质量配置切换

质量文件与参数语义见[配置参考](../reference/configuration.md#hindsight-110-质量参数)，命令与副作用见
[脚本入口](../../scripts/README.md#记忆质量配置)。Compose、SDK 和实际目标服务必须一起匹配 1.1.0。
旧版本目标需先按前文空目标切换流程处理，不能把本地 Compose 标签当作已运行版本。
开发部署不保留旧记忆时，可在专用 Hindsight PostgreSQL 中新建空数据库，通过私有 `.env`
的 `HINDSIGHT_DB_NAME` 选择它，再 recreate Hindsight；不需要迁移旧事实或向量。
应用数据库仍使用完整 SQL 链更新，不能删除账户和其他业务数据来代替记忆库切换。
`HINDSIGHT_API_WORKER_ID` 在 Compose 中固定，避免 recreate 后原生后台任务丢失恢复身份。

1. 修改 hindsight.yaml / prompts/memory.yaml，运行 memory-config.py validate。模型路由变更还需
   check/model-map.py 的脱敏检查；只验证配置不调用真实 Provider。
2. 暂停新的记忆动作消费，排空既有 memory_actions 和原生提取/归纳任务；停止所有旧、新发送者。
   retain_memory 尚未登记为动作的积压可留待恢复后冻结新 profile。uncertain 没有对账完成时不切换。
3. 使用显式目标环境运行 plan，核对 effective/override/matches。确认目标和差异后运行 apply，
   指定全新受限备份文件并声明 --senders-stopped。即使 YAML 已变，旧 Bank 也必须显式同步；
   不等待只有历史记忆的账户未来再发一次 Retain。若首次 Bank 初始化中断，仅允许对尚未发送、
   已冻结同一期望 profile 的动作修复该配置；不能据此切换旧动作语义或放过 unknown 发送者。
4. 服务模型/向量/重排配置变更需在已授权部署目标重建对应 Hindsight 服务；Worker 配置或提示词
   变更需重载同版本 Worker。SDK 依赖变化须重建 Worker/API 镜像，单纯重载源码不会升级依赖。
   保留原启动器的等待、信号转发和健康检查。更换 Embedding 模型/维度
   涉及历史向量和索引兼容，不能只改名称，须另做数据迁移方案。
5. inspect 回读确认 Bank 行为配置后恢复发送者，用新 Run 验证。Retain verified 不等于归纳已完成。
   失败时停发并保留原动作/UUID；restore 逐项恢复远端 override，再回退本地文件和对应服务模型环境。

不提供 Bank/服务质量参数热切：异步 Hindsight 后台任务可能读取后来配置，本地快照/hash 不能证明
远端按该版本执行；旧进程也可能覆盖新配置。配置更新不自动重提取历史事实或修复旧 Observation，
不得批量把 experience 改成 world。按来源定位后另行使用治理和受控重提取流程，保留 generation、
manual_curation、删除和撤权约束。真实 Provider 的中文语义质量另行评测，mock fixture 只证明结构与协议。

### PGroonga 目标与检查

`docker-compose.pgroonga.yaml` 为显式选择的独立 PG16 目标叠加文件，使用 `config/hindsight.pgroonga.yaml`，保留原 native 服务和数据卷。数据库镜像须具备锁定的 PGroonga/pgvector 扩展；修改 YAML 不会安装扩展或迁移已有列、索引和数据。`docker-compose.pgroonga.enabled.yaml` 只在目标、来源与治理检查完成后叠加，用于开启 Recall 和正常重启策略；不能将启动文件本身当作切换验收。

切换前按前述流程停发、排空并核对准确数据库，使用 `memory-cutover-check.py` 的显式容器、数据库主机和库名参数核验实际版本、扩展、列、完整索引及 Bank。该工具只读，不负责停发、备份或切换。当前检查器会将拒绝上传的 opt-out `blocked` 来源计作 `registered_document_missing`；须结合来源、原始拒绝事实、已处理 Outbox 和远端不存在逐项解释，不能靠删除审计来源或忽略其他异常解除阻断。

已接收 Retain 或治理增量的目标不得重复走空库切换，也不能直接回到旧空快照。日常验证优先使用只读检查和有授权的合成业务；更新宿主挂载的 Python 源码后，重载实际持有模块的 Worker/API，不能仅根据容器内文件 hash 判断运行进程已加载修复。


## SnowLuma / OneBot11

使用 SnowLuma 2.2.0 的已验证 amd64 digest（见 `.env.example`）；无旧渠道别名或登录态迁移。
`qq` profile 只启动 SnowLuma，Worker 的 `HPAGENT_CHANNELS` 默认为空。
在本地配置 `ONEBOT_EXPECTED_SELF_ID`，只指定本人账号/测试群验收，不在日志或文档粘贴令牌。
将选定 bot 号导出为终端环境变量 `ONEBOT_EXPECTED_SELF_ID`（脚本不自动读取 `.env`），
并在 Compose `.env` 中填写相同值。以下操作先保持 bot 停止；初始化不覆盖已有冲突配置、不重置登录密码：

```bash
python3 scripts/check/snowluma-version.py
python3 scripts/operations/snowluma-config.py --bot-id "$ONEBOT_EXPECTED_SELF_ID"
python3 scripts/operations/snowluma-config.py --bot-id "$ONEBOT_EXPECTED_SELF_ID" --check
# .env 设置 HPAGENT_CHANNELS=onebot11、ONEBOT_EXPECTED_SELF_ID；只运行一个 QQ Worker。
docker compose --profile qq up -d snowluma
# 本人在回环 noVNC 6081 / WebUI 5099 完成发行包协议确认与扫码。
# 完整迁移检查通过后，再重建相关 Worker，不能仅 restart 来更新环境变量。
docker compose up -d hpagent
```

令牌位于 `.data/onebot-secrets/onebot_access_token`；全局和唯一账号 snapshot 位于
`.data/snowluma/data/config/onebot*.json`，仅启用 Universal 反向 WS；HTTP/正向 WS、
自主状态回复、历史同步与主动通知关闭。WebUI 编辑后须停 bot 并重新 `--check`。
三个登录/配置挂载分别为 `.data/snowluma/data`、`qq-config`、`qq-share`；备份整个 `.data`。
挂载保留不等于重启后账号自动在线；重启或重建后仍须检查 login/version/status 与 QQ readiness。
若客户端回到扫码页，由本人重新登录，不能以容器存活或管理页面可打开判定恢复成功。
无 host 网络，8082/3000/3001 不发布；管理端口只回环，远程操作使用隧道。

Worker `/tmp/hpagent-qq-ready.json` 是脱敏诊断（可用 `scripts/check/onebot-status.py --path`
检查同进程命名空间中的文件）；它与 Web Worker health 分开，QQ 离线不代表 Web 应重启。
诊断区分监听、连接、版本、online/good、队列与 unknown/uncertain 计数。
OneBot 非持久入站队列，离线/溢出可能丢事件，不承诺补发。
控制回复只发匹配 bot 并等待 ack；持久通知保存每片实际 message_id。
accepted 仅代表供应商动作接受；超时/断线或模糊错误进入 uncertain，需要原有显式投递决议，
不得重跑 Run 或盲重发。旧连接/lease 的迟到 ack 不推进新发送。

渠道 CHECK 允许 `web`、`onebot11` 和 `official_qq`；完整迁移链与已应用 checksum 必须保留。
部署前检查迁移与当前目标类型一致；不能清空 Web/Work/文件数据或绕过不可变约束。
测试环境按独立 Compose project、资源标签和精确目录清理，保留当前 QQ 登录目录；
不能用业务 Compose 的 down -v 或全局 prune 清理测试资源。
尚未完成扫码、真实 QQ 收发、重建登录和 24 小时观察时，不得声称替换已验收上线。
