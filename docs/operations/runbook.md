# 运行手册

## 启动前

1. 从 `.env.example` 创建本地 `.env` 并填写数据库、Web 安全项和模型凭证。
2. `docker compose up -d` 启动基础设施。
3. 运行 migration，随后用 `scripts/bootstrap_identity.py` 建立 Web/QQ 身份绑定。
4. 按 `agent`、`web` 或 `web-prod` profile 启动业务进程。

## 健康检查

```bash
docker compose ps
curl -fsS http://127.0.0.1:8080/health/ready
docker compose logs --tail=200 hpagent hpagent-api
```

结构化日志位于 `.data/logs/`。优先用 `request_id`、`run_id` 或 `execution_id` 串联查询。

## 发布前验证

```bash
make lint
make typecheck
make test-existing
make test-db
make test-api
make ci-web
make e2e
```

PostgreSQL/Temporal E2E 必须在对应服务可用时执行；不要把缺少基础设施误报为代码通过。
