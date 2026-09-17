# 开发环境搭建

## 依赖

- Docker Engine 与 Docker Compose v2：运行受支持的服务拓扑。
- Python 3.11+：宿主机后端开发（CI 使用 Python 3.12）。
- Node.js 20+ 与 npm：宿主机前端开发。

## 环境与模型

```bash
cp .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

填写 `config/models.yaml` 引用的 Provider 环境变量。Secret 只放在 `.env`，不要写入 YAML。对外暴露服务前，检查 Feature Flag 并替换开发环境安全值。

## 首次启动

```bash
docker compose --profile web up -d --build
docker compose --profile web ps
curl --fail http://127.0.0.1:8080/health/ready
```

Web Profile 包含应用 PostgreSQL、Temporal PostgreSQL、Redis、Hindsight、SearXNG、Gotenberg、Migration Job、API、主 Worker、Document Worker 和 Vite 前端。Migration 是 API 与 Worker 的启动依赖。显式执行：

```bash
docker compose --profile web up hpagent-migrate
```

## 宿主机前端

```bash
make web-install
make web-dev
```

Vite 监听 <http://127.0.0.1:5173>，并根据 `web/vite.config.ts` 代理 API。完整开发栈优先使用 Compose 的 `web-dev` 服务。

## 可选服务

```bash
docker compose --profile tools up -d temporal-web  # http://127.0.0.1:8088
docker compose --profile qq up -d napcat
./scripts/dev/mcp.sh
```

开发时使用 `./scripts/operations/logs.sh` 查看日志；使用 `docker compose --profile web down` 停止服务。
