# Temporal Reference

| Task queue | Owner | Representative workflows/activities |
| --- | --- | --- |
| `hpagent-web-lifecycle` | main worker | `AgentLifecycleWorkflow`, Research workflows, Artifact build, lifecycle activities. |
| `hpagent-web-agent` | main worker | `AgentRunWorkflow`, ReAct, Plan-and-Execute, agent step/tool child workflows, agent activities. |
| `hpagent-task-queue` | main worker | reflection and metrics workflows/activities. |
| `hpagent-document` | document worker | heavy document normalization activity. |

The main worker connects one Temporal client and hosts lifecycle, agent, and scheduled-memory worker contexts. The document process connects separately because it is an intentional resource boundary.

Workflow identifiers are stable business correlation keys. Activity inputs carry Run and operation identity; retry safety comes from PostgreSQL idempotency/operation records, leases, and fencing rather than from assuming an activity runs once.

For an optional UI:

```bash
docker compose --profile tools up -d temporal-web
```

Open <http://127.0.0.1:8088>. CLI health:

```bash
docker compose exec temporal tctl --address localhost:7233 cluster health
```
