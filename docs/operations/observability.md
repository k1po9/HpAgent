# 可观测性

当前版本使用 `common.logging.log_event` 输出 JSONL 结构化生命周期事件，不引入 Prometheus、Grafana 或 OpenTelemetry Collector。

## Correlation fields

存在时记录：`request_id`、`run_id`、`execution_id`、`workflow_id`、`conversation_id`、`session_id`、`account_id`、`surface`、`component`、`event`、`status`、`elapsed_ms`、`error_code`。工具增加 `tool_call_id/tool/turn`；模型增加 `model/provider/turn`。

规则是“有则写、无则省略”。QQ execution 不伪造 `run_id`。

## 生命周期事件

| 边界 | 主要事件 |
|---|---|
| API | `request_received` 及 HTTP access/security 日志 |
| Run | `run_created/completed/failed/cancelled` |
| Workflow/Outbox | `temporal_workflow_starting/started`、`outbox_event_*` |
| Context | `context_assembly_*`、`memory_recall_*` |
| Agent | `agent_execution_started/completed/failed` |
| Model | `model_call_started/completed/failed` |
| Tool | `tool_execution_started/completed/failed` |
| Memory | `memory_retain_started/completed/failed/skipped` |
| SSE | `sse_subscribed/disconnected/terminal_*` |

后续接入 metrics/tracing 时应复用这些生命周期和关联字段，不重新定义业务状态。
