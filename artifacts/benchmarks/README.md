# HpAgent Benchmark Evidence Index

This directory is the committed evidence bundle for the three resume experiments. Start with
[`FINAL_ANALYSIS.md`](FINAL_ANALYSIS.md), then use the raw CSV/JSON files for independent review.

## 1. Temporal Durable Agent recovery

- `temporal_recovery_report.md`: Workflow Worker SIGKILL method, metrics, and limitations.
- `temporal_recovery_summary.json`: machine-readable aggregate for 50 trials.
- `temporal_recovery_trials.csv`: one row per Workflow Worker fault injection.
- `activity_worker_recovery_report.md`: Activity Worker SIGKILL and ack-gap interpretation.
- `activity_worker_recovery_summary.json`: machine-readable aggregate for 20 trials.
- `activity_worker_recovery_trials.csv`: one row per Activity Worker fault injection.

## 2. Transactional Outbox recovery

- `outbox_recovery_report.md`: method, aggregate results, and scope boundary.
- `outbox_recovery_summary.json`: machine-readable aggregate for 30 requests.
- `outbox_recovery_trials.csv`: one row per accepted request.

## 3. ReAct vs Plan-and-Execute

- `agent_strategy_report.md`: generated difficulty-level table using evaluable records.
- `agent_strategy_summary.json`: machine-readable aggregate and paired outcomes.
- `agent_strategy_trials.csv`: canonical 60-slot table; six environment-failure rows are retained.
- `agent_strategy_trials.jsonl`: append-only audit history, including the interrupted/retried record.
- `agent_strategy_tasks.yaml`: task manifest is under `scripts/benchmarks/`.
- `agent_strategy_gate_pilot_*.jsonl`, `agent_strategy_pilot*.jsonl`: environment qualification history.
- `agent_strategy_latency_pilot_report.md`: early provider latency and qualification failure.
- `agent_strategy_plan_logging_bug.md`: production logging bug discovered and fixed before formal run.

## Consolidated outputs

- `FINAL_ANALYSIS.md`: authoritative cross-experiment analysis and resume-safe claims.
- `FINAL_SUMMARY.json`: compact machine-readable headline metrics and validity notes.
- `HpAgent_resume_benchmark_report.md`: concise resume-oriented report.

All benchmark artifacts were generated on branch `feat/hpagent-web`. The raw evidence is retained;
environment failures are labeled rather than deleted or rewritten.
