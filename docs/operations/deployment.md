# 部署

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
cp .env.example .env
docker compose --profile web-prod up -d --build
docker compose --profile web-prod ps
curl --fail http://127.0.0.1:8080/health/ready
curl --fail http://127.0.0.1:${WEB_GATEWAY_PORT:-80}/
```

对外暴露前需要配置生产 Secret、Provider Credential、`WEB_PUBLIC_ORIGIN`，并按部署环境设置 `WEB_COOKIE_SECURE=true`。Compose 将 PostgreSQL、Redis、Temporal、SearXNG、Gotenberg、Hindsight 和 API 绑定在 Loopback；Gateway 是预期的公网入口。

离线镜像传输使用 `scripts/operations/docker-offline-export.sh` 和 `scripts/operations/docker-offline-load.sh`。

Compose 把 `scripts/operations/start-hindsight.sh` 挂载为 Hindsight 启动入口，并配置专用的 `hindsight-postgres` 与 `HINDSIGHT_API_DATABASE_URL`；当前部署不使用 embedded pg0。

## 旧架构数据库切换

054 / 055 / 057 的开发阶段迁移分别检查旧 Run / Task、Run、Work / Artifact 存储的空条件；058 加入有限子 Execution。没有历史 Task / Work 双写、backfill 或运行时兼容开关。`hpagent-migrate` 自动执行 schema 历史，不等于允许对任意旧业务库原位升级。迁移 checksum 不得修改来绕过检查。

切换前核对代码版本、完整迁移清单、数据库内容与仍在运行的 Temporal Workflow。保留应用 PostgreSQL、File Store、Temporal 和配置的匹配备份，停止旧 ingress / producer，明确旧 Workflow 的处置。需要保留历史业务数据时先另行设计数据转换；当前迁移未提供该路径。可丢弃开发环境可以建立新空数据库/卷，运行完整迁移链并显式切换 DSN；不把清空现有数据库作为启动步骤。

新环境先验证 schema、API ready、主 Worker healthy、Document poller，然后用账户只读查询与一条受控流程验证。已完成一次本地切换不等于所有部署环境已升级。各阶段的部署与恢复限制见[实施索引](../implementation/README.md)。

## 发布与健康验证

宿主机源码挂载变化需要对应 API / Worker 重载；镜像或 environment 变化使用 Compose recreate / build，`restart` 不读取新的容器环境。

```bash
docker compose --profile web-prod up -d --build hpagent hpagent-document-worker hpagent-api web-gateway
docker compose --profile web-prod ps
curl --fail http://127.0.0.1:8080/health/ready
docker compose exec -T hpagent python -m orchestration.worker_health
```

API ready 不能证明 Worker 已校验 Workflow 或能执行 Run。连接模型服务、真实 QQ 渠道和生产默认 Research lease 的恢复验证也分别进行；健康端点通过不替代这些验收。前端操作见[功能指南](web-workbench.md)。
