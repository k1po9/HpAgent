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
docker compose --profile web-prod up -d --build
docker compose --profile web-prod ps
curl --fail http://127.0.0.1:8080/health/ready
curl --fail http://127.0.0.1:${WEB_GATEWAY_PORT:-80}/
```

对外暴露前需要配置生产 Secret、Provider Credential、`WEB_PUBLIC_ORIGIN`，并按部署环境设置 `WEB_COOKIE_SECURE=true`。Compose 将 PostgreSQL、Redis、Temporal、SearXNG、Gotenberg、Hindsight 和 API 绑定在 Loopback；Gateway 是预期的公网入口。

离线镜像传输使用 `scripts/operations/docker-offline-export.sh` 和 `scripts/operations/docker-offline-load.sh`。

Compose 把 `scripts/operations/start-hindsight.sh` 挂载为 Hindsight 启动入口，并配置专用的 `hindsight-postgres` 与 `HINDSIGHT_API_DATABASE_URL`；当前部署不使用 embedded pg0。

## 空库初始化

本项目首次上线，不保留旧开发/测试业务数据，不执行旧库转换或历史数据回填。生产使用独立的空数据库/卷，不能把当前日常或测试卷当作生产输入。

`persistence/docker-init/` 创建运行角色，`hpagent-migrate` 执行 `persistence/migrations/` 的完整 SQL 链，创建当前表、索引和权限。文件名虽然叫 Migration，也是从零建库的必要输入；API/Worker 启动时还会校验 manifest/checksum，因此保留初始化器和全部 SQL。

先确认初始化服务成功退出，再检查 API ready、主 Worker healthy 和 Document poller；之后创建首个账户并执行真实模型流程。无需导入旧业务备份；投产后的数据保护见[备份恢复](backup-restore.md)。

## 发布与健康验证

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
