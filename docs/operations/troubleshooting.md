# 故障排查

| 症状 | 首要检查 |
|---|---|
| Worker 启动时报 `WORKER_DATABASE_URL` | 统一身份后端为必需项，检查 worker DSN 和 migration |
| QQ 提示账号未绑定 | 检查 `identity_bindings` 的 provider、normalized subject、verified/status 字段；运行 bootstrap 脚本 |
| Web run 长期 active | 查 Outbox lease、Temporal workflow、Reconciler 和 `run_id` 日志 |
| SSE 断开 | 客户端按 cursor 重连；检查 Redis 和 terminal snapshot |
| `workspace_recovery_required` | 检查账号 workspace、Git repo、process lock 与 isolation topology |
| 记忆缺失 | 查 `memory_recall_*`/`memory_retain_*`、Hindsight health 与 `account_id` |
| 工具超时 | 区分 `tool_timeout` 与整个执行的 `run_timeout` |

同一失败在不同边界应呈现不同事件，例如 `model_call_failed`、`agent_execution_failed`、`activity_failed`、`run_failed`。排查时不要只按错误文本聚合。
