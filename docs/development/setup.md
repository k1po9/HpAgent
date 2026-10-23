# 开发环境搭建

## 依赖

- Docker Engine 与 Docker Compose v2：运行受支持的服务拓扑。
- Python 3.11+：宿主机后端开发（CI 使用 Python 3.12）。
- Node.js 22+ 与 npm：宿主机前端开发。
- GNU Make：使用`make`快捷命令时需要；也可按[脚本索引](../../scripts/README.md)执行对应命令。

## 环境与模型

```bash
cp -n .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

填写 `config/models.yaml` 引用的 Provider 环境变量。Secret 只放在 `.env`，不要写入 YAML。对外暴露服务前，检查 Feature Flag 并替换开发环境安全值。

## 首次启动

```bash
WEB_PUBLIC_ORIGIN=http://localhost:5173 WEB_COOKIE_SECURE=false docker compose --profile web up -d --build
docker compose --profile web ps
curl --fail http://127.0.0.1:8080/health/ready
docker compose exec -T hpagent python -m orchestration.worker_health
```

Web Profile 包含应用 PostgreSQL、Temporal PostgreSQL、Redis、Hindsight、SearXNG、Gotenberg、Migration Job、API、主 Worker、Document Worker 和 Vite 前端。Migration 是 API 与 Worker 的启动依赖。首次上线使用独立空库，保留完整 SQL 初始化链，见[首次部署](../operations/deployment.md)。显式执行：

```bash
docker compose --profile web up hpagent-migrate
```

## 宿主机前端

```bash
make web-install
HPAGENT_API_TARGET=http://127.0.0.1:8080 make web-dev
```

若Compose的5173已占用，宿主开发使用 `cd web && HPAGENT_API_TARGET=http://127.0.0.1:8080 npm run dev -- --port 5274 --strictPort`，并让独立开发API的`WEB_PUBLIC_ORIGIN`与该浏览器origin一致；不要复用Cookie/Origin不匹配的业务API。

Vite 监听 <http://127.0.0.1:5173>，并根据 `web/vite.config.ts` 代理 API。完整开发栈优先使用 Compose 的 `web-dev` 服务。

## 可选服务

```bash
docker compose --profile tools up -d temporal-web  # http://127.0.0.1:8088
docker compose --profile qq up -d napcat
./scripts/dev/mcp.sh
```

开发时使用 `./scripts/operations/logs.sh` 查看日志；使用 `docker compose --profile web down` 停止服务。

首次人工操作见[功能指南](../operations/web-workbench.md)。首次打开后通过注册创建带密码的Web账户；管理员bootstrap仅绑定身份，不创建密码。测试前另建隔离数据库并显式设置三个同库DSN与E2E隔离标记，详见[测试](testing.md)；不要让测试 fixture 清理已登录账户的业务环境。

## Web Retriever 配置

Web Retriever v1 已冻结，模块设计与边界见[设计总结](../architecture/capabilities.md#web-retriever-v1-设计总结)。Worker 读取 `WEB_RETRIEVAL_SEARCH_PROVIDER=legacy|deepseek`、`WEB_RETRIEVAL_FETCH_PROVIDER=local|deepseek` 和既有密钥 `DEEPSEEK_API_SEARCH_FETCH`；由入口/容器注入，底层不加载 dotenv。默认 legacy/local，新环境推荐 DeepSeek Search + Local Fetch；缺少所选供应商或密钥时启动失败，不隐式切换。

Main 使用 web_search/web_fetch，provider=auto 读取配置，quick/standard 控制大小；本地默认 Text，进程内 `FetchRequest(format="markdown")` 可显式选择 Markdown v3。DeepSeek Fetch 仅支持 Markdown 且最终 URL 未知，严格来源 Research 保持 local。共享本地抓取需要公网直连，不提供代理或浏览器回退。配置修改与部署生效分别处理。
