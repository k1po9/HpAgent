# Web Lifecycle 与 Token Usage

HpAgent Web 对运行状态、调度、调试信息和资源用量使用不同的真相源：

| 数据 | 权威来源 | 在线投影 |
|---|---|---|
| Run 状态与最终消息 | PostgreSQL `runs` / `messages` | SSE / Zustand |
| Durable orchestration | Temporal History | Worker runtime |
| Agent 调试树 | `trace_runs` / `trace_events` | `trace.event` / TraceStore |
| Run Token accounting | `run_usage_ledger` / `run_budgets` | `RunSnapshot.run.budget` |
| 当前 progress | 无持久化真相 | Redis → SSE |

Token 总量必须来自 RunSnapshot，不能由前端累加 Trace。Trace 中的
`LLMCall.token_usage` 只用于解释单次调用。`used` 与 `reserved` 分开显示；没有
provider usage 且无法可信估算的失败请求会释放 reservation，并作为 unmetered
attempt 呈现。

Provider attempt ID 包含 Temporal Activity attempt、当前 scope 内调用序号、fallback
序号和 endpoint hash。业务 operation ID 继续负责 durable dedup；两者不可合并。

运行中的 LLM Trace 事件只作为刷新提示。前端重新读取 `GET /api/v1/runs/{run_id}`
后仅合并 `budget`，避免数据库中的 pending message 覆盖尚未持久化的 SSE delta。

新语义发布时将 `RUN_BUDGET_POLICY_VERSION` 设置为 `web-token-v2`。历史 estimated
记录不做猜测式回填。
