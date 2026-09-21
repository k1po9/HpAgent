# 部署

## Compose 拓扑

核心基础设施服务：`app-postgres`、`redis`、`temporal-postgres`、`temporal`、`hindsight-postgres`、`hindsight`、`searxng` 和 `gotenberg`。

应用服务：

- `hpagent-migrate`：一次性应用 Schema Migration。
- `hpagent-api`：FastAPI 命令接收、查询、认证、文件、Artifact、Research 与 SSE。
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
