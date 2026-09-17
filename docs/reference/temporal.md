# Temporal 参考

| Task Queue | 所有者 | 代表性 Workflow/Activity |
| --- | --- | --- |
| `hpagent-web-lifecycle` | 主 Worker | `AgentLifecycleWorkflow`、Research Workflow、Artifact Build、Lifecycle Activity。 |
| `hpagent-web-agent` | 主 Worker | `AgentRunWorkflow`、ReAct、Plan-and-Execute、Agent Step/Tool Child Workflow 与 Agent Activity。 |
| `hpagent-task-queue` | 主 Worker | Reflection 与 Metrics Workflow/Activity。 |
| `hpagent-document` | Document Worker | Heavy Document Normalization Activity。 |

主 Worker 使用一个 Temporal Client 承载 Lifecycle、Agent 和 Scheduled Memory Worker Context。Document Process 单独连接，因为它是有意设置的资源边界。

Workflow ID 是稳定的业务关联 Key。Activity Input 携带 Run 与 Operation Identity；重试安全来自 PostgreSQL Idempotency/Operation Record、Lease 和 Fencing，而不是假设 Activity 只运行一次。

可选 UI：

```bash
docker compose --profile tools up -d temporal-web
```

打开 <http://127.0.0.1:8088>。CLI 健康检查：

```bash
docker compose exec temporal tctl --address localhost:7233 cluster health
```
