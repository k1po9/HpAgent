# Transactional Outbox 宕机恢复实验

## 实验目标

验证 dispatcher 和 Temporal Web Worker 不可用期间，Web API 已接受的请求能否依靠 PostgreSQL
事务与 Outbox 在 replacement Worker 启动后最终处理，且不丢失 Run、不重复创建 Workflow。

## 实验设计

1. 使用隔离数据库和 Temporal namespace，停止 Outbox dispatcher/Worker。
2. 通过真实 Web API middleware、幂等键检查和 command transaction 接受 30 个请求。
3. 确认 Run、message 和 `start_run` Outbox event 已持久化。
4. 启动真实 replacement Worker，使用生产 claim/ack 和 deterministic Workflow ID 消费 backlog。
5. 验证每个请求对应一个完成的 Workflow 和 terminal Run。

Agent Activity 使用 benchmark fake model，避免把模型供应商波动混入 Outbox 恢复指标。

## 指标与结果

| 指标 | 结果 |
|---|---:|
| accepted/persisted requests | 30/30 |
| eventually processed Runs | 30/30 |
| Temporal completed Workflows | 30/30 |
| lost Runs | 0 |
| duplicate Workflows | 0 |
| backlog drain time | 11,916.932 ms |
| completion latency P50/P95 | 9,718.245 / 12,664.568 ms |

## 证据与复现

- [`report.md`](report.md)：详细方法、局限和复现命令。
- [`summary.json`](summary.json)：指标定义与机器可读聚合。
- [`trials.csv`](trials.csv)：30 个请求的逐条证据。
- 脚本：`scripts/benchmarks/outbox/outbox_recovery_benchmark.py`。
