# 日志

## Compose 日志

统一使用仓库日志入口，无需记忆 Profile 参数：

```bash
./scripts/operations/logs.sh                         # 实时查看全部服务
./scripts/operations/logs.sh --api                   # FastAPI
./scripts/operations/logs.sh --worker                # 主 Worker + Document Worker
./scripts/operations/logs.sh --qq                    # Worker + NapCat
./scripts/operations/logs.sh --infra                 # 数据与持久化服务
./scripts/operations/logs.sh --api -n 100 --no-follow
./scripts/operations/logs.sh temporal redis -n 200
```

直接使用 Compose 也有效：`docker compose --profile web logs -f --tail 200 hpagent` 和 `docker compose --profile web logs -f --tail 200 hpagent-api`。

## 结构化应用日志

主 Worker 写入 `.data/logs/hpagent.jsonl` 和 `.data/logs/hpagent-error.log`；API 写入 `.data/logs/web-api.jsonl` 和 `.data/logs/web-api-error.log`。JSONL 包含 `ts`、`level`、`logger`、`msg`；Lifecycle Event 还包含 `event`、`component` 和关联字段。

```bash
jq 'select(.run_id == "RUN_UUID")' .data/logs/*.jsonl
jq 'select(.conversation_id == "CONVERSATION_UUID")' .data/logs/*.jsonl
jq 'select(.workflow_id == "WORKFLOW_ID")' .data/logs/*.jsonl
jq 'select(.operation_id == "OPERATION_ID")' .data/logs/*.jsonl
jq 'select(.level == "ERROR")' .data/logs/*.jsonl
```

只有 Container 输出时：

```bash
./scripts/operations/logs.sh --worker --no-follow | grep -F 'RUN_UUID'
./scripts/operations/logs.sh --qq --no-follow | grep -F 'MESSAGE_OR_DELIVERY_ID'
```

## 追踪一次 Run

1. 从 API Response、SSE Event 或 QQ Delivery Row 获取 `run_id`。
2. 在 JSONL 或 `GET /api/v1/runs/{run_id}`、`/trace` 中查找 `conversation_id`、执行事件和 `workflow_id`。
3. 使用 `operation_id` 追踪 Tool、File、Research 或 Document 副作用。
4. Run 恢复或 Worker 重启时，检查 `lease_token`/Fencing Event。

QQ Ingress/Delivery 还可能包含 Provider Message Identity、`delivery_id` 和 `msg_seq`。先搜索标准化 Provider Message ID，再转到 `run_id`。

## 区分执行失败与投递失败

执行失败会让 Run 进入 Failed，并出现在 Lifecycle/Agent Event 中。投递失败发生在结果提交之后：Run 可能已 Completed，但 `qq_deliveries` 状态为 `failed` 或 `uncertain`。此时检查 Worker Delivery Log 与 PostgreSQL Delivery State；重试投递不能创建新 Run。

`LOG_LEVEL` 控制 Console 日志级别。JSONL 保留 DEBUG 及以上日志，每日轮转并保留 30 份；部分 Compose Container 日志按大小轮转。
