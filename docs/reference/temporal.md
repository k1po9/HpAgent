# Temporal 参考

| Task Queue | 所有者 | 当前 Workflow / Activity |
| --- | --- | --- |
| `hpagent-web-lifecycle` | 主 Worker | `AgentLifecycleWorkflow`、`NormalizeDocumentWorkflow` 与 Lifecycle Activity；reminder、research_report、artifact_html 由有限 Run 入口调度。 |
| `hpagent-web-agent` | 主 Worker | `AgentRunWorkflow`、`ReactAgentWorkflow`、`PlanAndExecuteWorkflow`、`AgentStepWorkflow`、`ToolExecutionWorkflow`、`WorkDelegationWorkflow` 与 Agent / delegation Activity。 |
| `hpagent-task-queue` | 主 Worker | `ReflectWorkflow`、`MetricsReportWorkflow` 与对应 Activity。 |
| `hpagent-document` | Document Worker | Heavy Document Normalization Activity，Workflow 编排仍在生命周期队列。 |

主 Worker 使用一个 Temporal Client 和多个 Worker Context。Document Process 单独连接，形成有意的资源边界。Research graph 在统一 Lifecycle identity 内运行，没有第二个 Research 启动入口；Artifact HTML 是注册 executor 的 Activity，没有独立 Artifact Build Workflow。

Run Workflow ID 固定为 `hpagent-web-run-{run_id}`，Generic Agent 子 Workflow 为 `hpagent-agent-run-{run_id}`。Activity Input 关联 Run / Execution / operation；安全性来自持久化 attempt / receipt、幂等、lease 与 fence，不是假设 Activity 只运行一次。

## Work 调度与 Workflow 确定性

Work 使用 PostgreSQL schedule / occurrence / wakeup evaluator，不注册 Temporal Schedule。Memory reflection / metrics 仍注册 Temporal Schedule。time / revision / enablement 变更由 Work schedule version 阻断旧 occurrence。

Workflow 禁止 I/O。仅供 Activity 使用的网络库不能在 Workflow 的间接 import 链初始化；错误分类模块只在实际分类调用时加载 HTTP 依赖。`test/test_durable_agent_contract.py` 对 8 个注册 Workflow 使用真实 `SandboxedWorkflowRunner.prepare_workflow`，避免仅用 mock Registry 漏掉启动失败。

## 查看与健康检查

```bash
docker compose --profile tools up -d temporal-web
docker compose exec temporal tctl --address localhost:7233 cluster health
docker compose exec -T hpagent python -m orchestration.worker_health
```

UI 位于 <http://127.0.0.1:8088>。主 Worker ready 要求 schema 验证、Temporal 连接、Lifecycle / Agent Worker 及 Dispatcher 已启动。API ready 不能替代 Worker 健康。恢复限制见[可靠性](../architecture/reliability.md)，实现与验收见[实施索引](../implementation/README.md)。
