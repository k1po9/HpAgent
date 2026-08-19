# Activity Worker Recovery and Ack-Gap Report

## Environment

- Git commit: `3c192760c30dae6a9125926b46c7a3c3d993ccb5`
- Branch: `feat/hpagent-web`
- Timestamp: `2026-08-18T15:26:46.257093+00:00`
- Python: `3.12.3`
- Temporal Python SDK: `1.31.0`
- Temporal namespace: `hpagent-activity-benchmark-final`
- PostgreSQL database: `hpagent_activity_benchmark`

## Method

Every trial kept a real Workflow Worker alive, killed a separate Activity Worker process with
SIGKILL at an explicit boundary marker, started a replacement Activity Worker, and observed the
same Temporal Activity retry. A1 killed before any effect. A2 killed after an idempotent state
write but before Activity completion. A3 used the production DurableAgentActivities,
AgentDataStore operation intent, fencing token, non-idempotent classification, and unsupported
reconciler. Its external effect counter lived in an independent SQLite file.

## Results

- Trials: **20**
- Safe outcomes: **20**
- Safe outcome rate: **100.00%**
- Activity retry observed: **20**
- Activity retry observed rate: **100.00%**
- Unexpected failures: **0**
- Kill-to-terminal latency P50: **3998.718 ms**
- Kill-to-terminal latency P95: **4129.283 ms**

| Case | Trials | Safe | Eventually completed | Retry observed |
|---|---:|---:|---:|---:|
| A1 pre-side-effect | 5 | 5 | 5 | 5 |
| A2 idempotent ack gap | 5 | 5 | 5 | 5 |
| A3 non-idempotent ack gap | 10 | 10 | 0 | 10 |

### A3 Safety Outcomes

- Safe reconciled success: **0**
- Uncertain fail-closed: **10**
- Unsafe duplicate side effects: **0**
- Unsafe duplicate side-effect rate: **0.00%**

## Interpretation and Boundary

A3 safe failures are not Workflow recovery successes: the external write occurred once, its
Activity completion was lost, and unsupported reconciliation forced the stable operation into
`uncertain` with a non-retryable `tool_side_effect_uncertain` failure. This is fail-closed safety,
not exactly-once delivery and not automatic business reconciliation.

## Failures

Unexpected failure count: **0**. Raw details are retained in the CSV and
JSON summary.

## Reproduction

```bash
docker compose up -d app-postgres temporal-postgres temporal
PYTHONPATH=src TEMPORAL_HOST=localhost:7233 \
  TEMPORAL_NAMESPACE=hpagent-activity-benchmark-final \
  .venv/bin/python \
  scripts/benchmarks/temporal/activity_worker_recovery_benchmark.py
```
