# Execution Observatory V1 (P0)

Execution Observatory 是本地只读调试工具，用于把 Web 与 QQ 的执行生命周期放在同一个页面中检查。它不写业务状态，不执行恢复，不连接 Temporal、Redis 或 Hindsight 的在线诊断接口。

## 启动

先启动所需基础设施和应用，然后在项目根目录执行：

```bash
.venv/bin/python scripts/observability-viewer.py
```

打开：

```text
http://127.0.0.1:8091
```

常用参数：

```bash
.venv/bin/python scripts/observability-viewer.py \
  --host 0.0.0.0 \
  --port 8091 \
  --log-dir .data/logs \
  --database-url postgresql://hpagent_api:hpagent_api@127.0.0.1:5434/hpagent
```

数据库地址读取顺序：`OBSERVABILITY_DATABASE_URL`、`APP_DATABASE_URL`、`WORKER_DATABASE_URL`，最后使用本地 Compose 默认地址。所有 Observatory 查询均在只读事务中执行。

## P0 数据源

| 数据源 | 内容 | 语义 |
|---|---|---|
| `.data/logs/*.jsonl` | Agent、Model、Tool、Memory、API、SSE 生命周期 | OBSERVED |
| PostgreSQL | Web Run、Message、Session、Conversation | AUTHORITATIVE |
| PostgreSQL | Outbox、workflow execution 投影 | DURABLE |
| QQ `.wal` / `history.jsonl` | QQ 会话事件 | DURABLE |
| Execution Waterfall | 生命周期配对与状态归一 | DERIVED |

JSONL 使用增量 offset 读取，并处理文件轮转和缩短；内存默认最多保留 30,000 条事件。PostgreSQL 列表只读取最近 100 个 Run，详情按需读取。QQ 文件按 mtime/size 变化刷新，归档 `history.jsonl` 优先于活跃 WAL。

## Correlation

Web 使用 `execution_id`，缺失时使用 `run_id`；当前 Web 中二者相等。QQ 使用 `execution_id=qq-turn-*`，不会创建假的 `run_id`。

QQ WAL 中每轮带 `execution_id` 的 `user_message` 是分段起点，该事件到下一条带 execution 的 `user_message` 之前的 model/tool/memory 事件属于同一轮。

## 页面

- Execution List：按最新活动时间排序，可按 surface、status、component 和关联字段搜索。
- Execution Waterfall：配对 `*_started` 与 `*_completed/failed/degraded`，显示耗时、turn、tool 和 error code。
- Raw Events：保留原始 JSON，可按 component、event、failed/degraded 筛选并复制。
- State Inspector：分开显示 PostgreSQL、QQ Session 与来源语义，避免混淆权威状态和观察事件。
- Live refresh：浏览器每秒轮询，无 WebSocket、无新增 Redis 数据结构。

## P0 明确不包含

Temporal 实时 inspect、Redis 在线历史、Hindsight health/metrics、Critical Path 百分比和完整 anomaly engine 属于后续阶段。页面会明确将这三个在线源标为 `P1 / not_connected`。

