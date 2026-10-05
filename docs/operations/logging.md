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

1. 从 API Response、SSE Event 或 Delivery Row 获取 `run_id`。
2. 在 JSONL 或 `GET /api/v1/runs/{run_id}`、`/trace` 中查找 `conversation_id`、执行事件和 `workflow_id`。
3. 使用 `operation_id` 追踪 Tool、File、Research 或 Document 副作用。
4. Run 恢复或 Worker 重启时，检查 `lease_token`/Fencing Event。

Workspace entry 可通过 `GET /api/v1/workspace/nodes/{node_id}/trace` 关联来源 Run/Work / requirement revision、版本和保存 operation，以及候选固定、物化与首次读取；`GET /api/v1/workspace/files/{file_id}/retention` 说明引用及物理字节。结构化事件 `workspace_request_rejected`、`workspace_cas_conflict`、`workspace_stop_unconfirmed`、`file_gc_retry` 只记录代码与对象 ID，不记录文件正文或存储凭据。

QQ Ingress/Delivery 还可能包含 Provider Message Identity、`delivery_id` 和 `msg_seq`。先搜索标准化 Provider Message ID，再转到 `run_id`。

## 区分执行失败与投递失败

执行失败会让 Run 进入 `failed`，并出现在 Lifecycle/Agent Event 中。投递失败发生在结果提交之后：Run 可能已 `succeeded`，但统一 `deliveries` 尚未 `accepted` 或处于 `uncertain`。此时检查 Worker Delivery Log 与 PostgreSQL Delivery State；重试投递不能创建新 Run。

`LOG_LEVEL` 控制 Console 日志级别。JSONL 保留 DEBUG 及以上日志，每日轮转并保留 30 份；部分 Compose Container 日志按大小轮转。

## Work、Execution 与模型关联

从 Work event / snapshot 找到 work_id、requirement_revision、active coordinator Run 与 control_epoch，再关联 Run、execution_id、operation_id、attempt / fencing token。子分支有独立 Execution，其用量是原 Run / Work ledger 的投影，不能重复计费。Work event_seq 和 Run SSE cursor 分别恢复，不混用。

ModelClient 的 `model_http_error` 记录 endpoint_id、provider、model、http_status 与允许的 provider_error_code / provider_request_id；`model_dispatch_failed` 记录错误类别和耗时。模型调用 scope 提供 run_id、operation_id、model_call_id、snapshot_id、phase、attempt。Model Decision 的分类错误贯通 Trace 与 API failure。

```bash
jq 'select(.work_id == "WORK_UUID")' .data/logs/*.jsonl
jq 'select(.execution_id == "EXECUTION_UUID")' .data/logs/*.jsonl
jq 'select(.snapshot_id == "SNAPSHOT_UUID")' .data/logs/*.jsonl
jq 'select(.event == "model_http_error" or .event == "model_dispatch_failed")' .data/logs/*.jsonl
```

Provider 错误自由文本不作为模型分发诊断字段；只保留受限 ASCII code / request ID，不输出凭据或文件正文。模型快照内容仍受 entitlement 可见性控制，full_safe 请求体不是额外脱敏承诺。排障以分类与关联记录为准，不能把连接、请求格式、响应解析或额度问题都解释为浏览器网络中断。见[故障排查](troubleshooting.md)。
