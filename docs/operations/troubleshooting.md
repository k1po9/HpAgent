# 故障排查

## API 未 Ready

```bash
docker compose --profile web ps
./scripts/operations/logs.sh hpagent-migrate hpagent-api app-postgres --tail 200 --no-follow
```

确认 Migration 成功、应用角色密码与 `.env` 一致，并且 `app-postgres` 健康。

## Run 一直处于 Queued

检查 `hpagent`、`temporal` 和 `app-postgres` 日志。确认 `hpagent-web-lifecycle` 与 `hpagent-web-agent` Worker 正在 Poll，Outbox Dispatcher 可通过 `WORKER_DATABASE_URL` 连接数据库。

## 模型调用失败

对照 `config/models.yaml` 检查 `.env` 中的 Provider 变量。Base URL、Key 或 Model Name 为空会导致对应链不可用。执行实时连接检查前先运行 `python scripts/check/models.py --help`。

## Memory 不可用

```bash
curl --fail http://127.0.0.1:8001/health
./scripts/operations/logs.sh hindsight hindsight-postgres --tail 200 --no-follow
```

检查 Hindsight LLM 以及 SiliconFlow Embedding/Rerank 变量。Memory 降级不会改变 PostgreSQL 的应用状态权威。

## Research 没有来源

检查 `curl --fail http://127.0.0.1:8085/`、`SEARXNG_URL`、代理设置和 `searxng` 日志。服务会根据 `config/searxng/settings.yml` 生成运行时配置。

## Document Normalization 卡住

检查 `hpagent-document-worker`、`temporal`、`gotenberg` 与 PostgreSQL 日志。确认 `hpagent-document` Activity Worker 正在 Poll，File Store 与 Document Run Volume 已挂载。

## QQ 没有回复

先判断 Run 是否完成：未完成则排查执行；已完成则检查 `qq_deliveries`、Worker Delivery Log、Provider Credential 和 NapCat/Official QQ 连接。Delivery Retry 不会重新执行 Run。
