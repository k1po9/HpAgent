# Temporal Recovery Benchmark Report

## Environment

- Git commit: `3c192760c30dae6a9125926b46c7a3c3d993ccb5`
- Branch: `feat/hpagent-web`
- Timestamp: `2026-08-18T05:32:10.607593+00:00`
- Python: `3.12.3`
- Temporal Python SDK: `1.31.0`
- Docker Compose: `5.3.1`
- Host: `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39`

## Workflow Worker Loss

### Method

Each trial starts one Activity Worker and one Workflow Worker as real OS processes. After the
configured Activity exposes its boundary marker, the runner sends SIGKILL to the Workflow Worker,
releases the Activity, starts a replacement Workflow Worker, waits for the same Temporal Workflow,
and checks transcript version, unique operation ids, operation attempts, and side-effect count.

### Results

- Trials: **50**
- Recovery success: **50**
- Recovery success rate: **100.00%**
- Duplicate-operation trials: **0**
- Duplicate-operation rate: **0.00%**
- Duplicate-side-effect trials: **0**
- Duplicate-side-effect rate: **0.00%**
- Recovery latency P50: **10485.01 ms**
- Recovery latency P95: **10683.786 ms**

### Results by Fault Boundary

| Case | Strategy | Fault boundary | Trials | Success | Success rate | P50 ms | P95 ms |
|---|---|---|---:|---:|---:|---:|---:|
| T1 | react | react-model-decision | 10 | 10 | 100.00% | 10516.122 | 10613.623 |
| T2 | react | react-tool-before-side-effect | 10 | 10 | 100.00% | 10474.946 | 10605.096 |
| T3 | react | react-tool-after-side-effect-before-ack | 10 | 10 | 100.00% | 10411.926 | 10530.92 |
| T4 | plan_and_execute | plan-step-2 | 10 | 10 | 100.00% | 10655.599 | 10748.09 |
| T5 | plan_and_execute | plan-final-evaluation | 10 | 10 | 100.00% | 10430.708 | 10471.494 |

### Failure Analysis

Failures are retained verbatim in `summary.json` and each trial's state directory.
Observed failure count: **0**.

### Limitations

T3 kills the Workflow Worker after the fake tool's externally visible counter has advanced but before the Activity result is released. The Activity Worker remains alive. The separate PostgreSQL acceptance test `test/web_persistence/test_durable_agent_activity_worker_kill.py` covers Activity Worker loss in the non-idempotent ack gap and expects fail-closed `uncertain` handling. The benchmark therefore demonstrates replay-safe Workflow recovery, not universal exactly-once execution for arbitrary external tools.

### Reproduction

```bash
docker compose up -d temporal-postgres temporal
docker compose exec -T temporal tctl --ns hpagent-benchmark namespace register --rd 1
PYTHONPATH=src TEMPORAL_HOST=localhost:7233 TEMPORAL_NAMESPACE=hpagent-benchmark \
  .venv/bin/python \
  scripts/benchmarks/temporal/temporal_recovery_benchmark.py --trials-per-case 10
```
