# 基准与故障恢复验证

本页说明仓库中三类基准的用途、复现入口和结果解释边界。脚本实现位于
[`scripts/benchmarks/`](../scripts/benchmarks/README.md)，已提交的报告与逐次证据位于
[`artifacts/benchmarks/`](../artifacts/benchmarks/README.md)。运行新的实验会改写对应输出目录，
需要保留历史结果时应先使用新的 `--output-dir` 或 `--output`。

## 实验矩阵

| 实验 | 验证对象 | 默认输出 | 是否调用真实模型 |
|---|---|---|---|
| Temporal Workflow Worker recovery | History replay 后恢复控制流，避免重复 operation/副作用 | `artifacts/benchmarks/temporal/workflow_worker/` | 否 |
| Temporal Activity Worker recovery | Activity retry、ack gap 与非幂等副作用 fail-closed | `artifacts/benchmarks/temporal/activity_worker/` | 否 |
| Transactional Outbox recovery | Worker 停机期间请求持久化、积压恢复与确定性 Workflow ID | `artifacts/benchmarks/outbox/` | 否，使用 benchmark fake model |
| Agent strategy | 相同模型与工具预算下对比 ReAct 和 Plan-and-Execute | `artifacts/benchmarks/agent_strategy/` | 是 |

## 通用前置条件

- 使用项目虚拟环境并安装 `requirements-dev.txt`。
- Temporal 实验使用专用 namespace；需要数据库的实验使用专用 PostgreSQL database，禁止指向生产库。
- 在仓库根目录运行脚本，并设置 `PYTHONPATH=src`。
- 基准目录保存原始样本；失败、环境错误和中断记录不得为改善指标而删除。

## Temporal Worker 恢复

Workflow Worker 实验在 ReAct model/tool 边界及 Plan-and-Execute step/evaluation 边界终止真实
Worker 进程，再由 replacement Worker 接管同一 Workflow：

```bash
PYTHONPATH=src TEMPORAL_HOST=localhost:7233 \
  TEMPORAL_NAMESPACE=hpagent-benchmark \
  .venv/bin/python scripts/benchmarks/temporal/temporal_recovery_benchmark.py
```

Activity Worker 实验额外需要可创建、删除专用测试数据库的管理员 DSN：

```bash
PYTHONPATH=src TEMPORAL_HOST=localhost:7233 \
  TEMPORAL_NAMESPACE=hpagent-activity-benchmark \
  ACTIVITY_BENCHMARK_ADMIN_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/postgres \
  .venv/bin/python scripts/benchmarks/temporal/activity_worker_recovery_benchmark.py
```

默认样本数分别为 50 次和 20 次。详细 case、指标与当前结果见
[`artifacts/benchmarks/temporal/README.md`](../artifacts/benchmarks/temporal/README.md)。

## Transactional Outbox 恢复

该实验在 dispatcher/Worker 不可用时通过真实 API middleware 和 command transaction 持久化
30 个请求，再启动 replacement Worker 清空 backlog：

```bash
PYTHONPATH=src TEMPORAL_HOST=localhost:7233 \
  TEMPORAL_NAMESPACE=hpagent-outbox-benchmark \
  OUTBOX_BENCHMARK_ADMIN_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/postgres \
  .venv/bin/python scripts/benchmarks/outbox/outbox_recovery_benchmark.py
```

结论只覆盖 PostgreSQL transaction、Outbox claim/ack、Temporal 编排与 Run 生命周期恢复；
Agent Activity 使用 fake model，不能据此推断模型供应商可靠性。详见
[`artifacts/benchmarks/outbox/README.md`](../artifacts/benchmarks/outbox/README.md)。

## Agent 策略实验

策略实验需要完整 Web/Durable Agent 服务、专用 benchmark 账号与 workspace，以及真实模型凭据。
先执行零模型调用的环境检查：

```bash
make agent-benchmark-check
```

检查通过后再运行实验：

```bash
make agent-benchmark-run
```

运行命令会再次检查环境，执行两条 pilot，然后启动 30 个任务 × 2 种策略的正式实验；默认支持
按 task/strategy/repetition 断点续跑。它会产生真实模型费用，安全与公平性前置条件见
[`scripts/benchmarks/agent_strategy/README.md`](../scripts/benchmarks/agent_strategy/README.md)。

## 结果解释

当前提交证据中的可靠性实验共 100 个观测，均达到各自预定义的恢复或安全终态。Activity A3
的预期结果是 `tool_side_effect_uncertain`：当非幂等外部调用已发生、但 completion ack 丢失且
无法 reconciliation 时，系统拒绝自动重放。它是安全失败，不是 exactly-once 成功。

策略实验只有一次重复；60 个槽位中有 6 个环境错误，正式性能口径为 27 组有效配对。因此当前
结果可用于形成“简单任务优先 ReAct、中等多步任务可考虑 Plan-and-Execute”的路由假设，不能
宣称存在普适胜者。跨实验摘要、机器可读指标与有效性限制见
[`artifacts/benchmarks/summary/FINAL_ANALYSIS.md`](../artifacts/benchmarks/summary/FINAL_ANALYSIS.md)。
