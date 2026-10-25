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
- `qq`：可选 NapCat 服务及依赖。
- `tools`：可选 Temporal UI 及依赖。

## 启动

```bash
cp -n .env.example .env
# 先按配置入口补齐全部凭据、预算、绑定地址和NapCat版本
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
