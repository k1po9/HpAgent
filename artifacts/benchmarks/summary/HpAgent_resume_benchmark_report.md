# HpAgent Resume Benchmark Report

## Scope and Environment

- Git commit: `3c192760c30dae6a9125926b46c7a3c3d993ccb5`
- Branch: `feat/hpagent-web`
- Host: WSL2 Linux, 8 logical CPUs
- Python: `3.12.3`
- Temporal Python SDK: `1.31.0`
- Benchmark date: `2026-08-18`
- All experiments used dedicated PostgreSQL databases and Temporal namespaces.

## 1. Temporal Recovery

### A. Workflow Worker SIGKILL

- 50 trials across five fault boundaries; recovery succeeded in **50/50 (100%)**.
- Duplicate logical operations: **0**; duplicate externally visible side effects: **0**.
- Kill-to-recovery latency: P50 **10485.010 ms**, P95 **10683.786 ms**.

### B. Activity Worker SIGKILL and Ack Gap

- 20 trials with a separate real Activity Worker process killed by SIGKILL; a replacement
  Activity Worker retried the same Activity in **20/20 (100%)** trials.
- A1, before side effect: **5/5** eventually completed with zero side effects.
- A2, idempotent effect succeeded before ack: **5/5** eventually completed; every trial had two
  physical Activity invocations but one final business-state effect.
- A3, non-idempotent external effect succeeded before ack: **10/10** entered the production
  `uncertain` fail-closed path after retry; every independently persisted external counter was
  exactly one and unsafe duplicate side effects were **0/10**.
- Kill-to-terminal latency: P50 **3998.718 ms**, P95 **4129.283 ms**.

The A3 result is a safety result, not an exactly-once claim: automatic reconciliation was
unsupported, so the Workflow failed closed with `tool_side_effect_uncertain` instead of risking a
second external write.

## 2. Transactional Outbox Recovery

- 30 requests were accepted and persisted while the dispatcher/Worker was unavailable.
- Eventually processed Runs: **30/30 (100%)**; Temporal Workflows completed: **30/30**; lost Runs:
  **0**; duplicate Workflows: **0**.
- Backlog drain time: **11916.932 ms**.
- Eventual completion latency: P50 **9718.245 ms**, P95 **12664.568 ms**.

## 3. Agent Strategy Experiment

The formal run recorded all 60 configured slots (30 tasks × 2 strategies) with MiniMax-M3,
provider-default temperature, and max turns 5. Six records for `complex_008`–`complex_010` were
environment failures caused by an out-of-scope fixture file in the isolated Git workspace. They are
preserved in raw evidence but excluded from evaluable performance metrics, leaving 27 paired tasks
per strategy.

| Difficulty | Strategy | Evaluable | Success | Success rate | Avg latency | P95 latency |
|---|---|---:|---:|---:|---:|---:|
| simple | ReAct | 10 | 9 | **90.0%** | 13.64 s | 20.69 s |
| simple | Plan-and-Execute | 10 | 5 | 50.0% | 23.59 s | 31.68 s |
| medium | ReAct | 10 | 4 | 40.0% | 18.68 s | 25.46 s |
| medium | Plan-and-Execute | 10 | 7 | **70.0%** | 26.32 s | 45.24 s |
| complex | ReAct | 7 | 2 | 28.6% | 28.27 s | 38.53 s |
| complex | Plan-and-Execute | 7 | 2 | 28.6% | 53.71 s | 104.57 s |
| overall | ReAct | 27 | 15 | **55.6%** | 19.30 s | 32.80 s |
| overall | Plan-and-Execute | 27 | 14 | 51.9% | 32.41 s | 55.46 s |

The paired outcomes were: both succeeded 10, ReAct only 5, Plan-and-Execute only 4, and both failed
8. Therefore the single-repetition run does not establish a universal winner. It does show a useful
routing pattern: ReAct was substantially better and faster on simple tasks; Plan-and-Execute gained
30 percentage points on medium tasks at a 41% average-latency cost; neither strategy was reliable on
the seven evaluable complex tasks under the five-turn configuration.

Plan-and-Execute averaged 6.852 model calls versus 3.148 for ReAct (2.18×), and 3.296 tool calls
versus 2.778 (1.19×). Two Plan-and-Execute simple-task failures were provider
`model_unavailable` errors. Excluding only those provider failures, its capability-conditioned
overall rate is 14/25 (56.0%), nearly equal to ReAct's 15/27 (55.6%). Token counts were unavailable.

## Resume-Ready Claims

- 基于 Temporal 构建 Durable Agent，并通过 50 次 Workflow Worker 与 20 次 Activity Worker
  SIGKILL 故障注入验证恢复与副作用安全：70/70 次达到预定义安全结果，未观察到额外重复外部副作用。
- 设计 Transactional Outbox 恢复链路，在 Worker 停机期间积压 30 个请求后实现 30/30 最终处理，
  丢失 Run 与重复 Workflow 均为 0，积压清空耗时约 11.9 秒。
- 对非幂等 ack gap 采用持久化操作意图、重试检测与 `uncertain` fail-closed 策略；10/10 次试验均
  阻止二次外部写入。该表述不宣称任意外部工具具备 exactly-once 语义。
- 在 27 组有效配对任务中，ReAct 在简单任务达到 90% 成功率且平均耗时 13.64 秒；
  Plan-and-Execute 在中等任务达到 70% 成功率、较 ReAct 高 30 个百分点，但平均耗时增加约 41%。
  复杂任务样本不足且两者均仅 2/7 成功，不作普适优劣结论。

## Evidence

- `../temporal/workflow_worker/{trials.csv,summary.json,report.md}`
- `../temporal/activity_worker/{trials.csv,summary.json,report.md}`
- `../outbox/{trials.csv,summary.json,report.md}`
- `../agent_strategy/{trials.csv,trials.jsonl,summary.json,report.md}`
- `FINAL_ANALYSIS.md` for the consolidated interpretation, validity boundary, and discovered bugs
