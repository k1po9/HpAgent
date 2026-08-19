# Agent Strategy Model-Latency Pilot

## Decision

**Formal experiment not started.** The current environment failed the acceptance gate: neither
strategy completed `simple_001`, provider connectivity repeatedly timed out, and native workspace
tools were disabled.

## Configuration

- Date: `2026-08-19` (Asia/Shanghai)
- Provider: `minimax`
- Model: `MiniMax-M3`
- Temperature: `provider-default` (the Worker sends no temperature field)
- Max turns: `5`
- Task: `simple_001`
- Trials: one `react`, one `plan_and_execute`
- Dedicated benchmark account and Git workspace; no personal account data used

## Results

| Strategy | Runner observation | Durable Run terminal state | Durable elapsed | Final error |
|---|---|---|---:|---|
| react | timed out at 300.836 s | failed | 329.684 s | `model_unavailable` |
| plan_and_execute | failed | failed | 138.997 s | `planning_failed` |

The ReAct operation history contained a context operation, a model operation completed after three
attempts, one tool operation, and a second model operation failed after three attempts. The Plan
history contained a context operation and a planning operation failed after three attempts.

## Root Cause Evidence

- A successful MiniMax chat response took about **5.072 s**, showing that successful-call latency
  alone can be acceptable.
- Multiple MiniMax requests failed with `httpx.ConnectTimeout` at the configured **30 s** timeout.
- Context recall rewriting also exhausted its fast-model fallback chain (MiniMax, SiliconFlow,
  Alibaba) in about **55.1 s** because every endpoint hit a connect timeout.
- Temporal retried failed model/planning Activities, amplifying intermittent provider/network
  failures into 139–330 s end-to-end failures.
- Worker logs reported `native tools disabled`; the file-reading task exposed unrelated MCP tools,
  and the successful ReAct decision selected `maps_search_detail`. This tool environment cannot
  objectively evaluate the prepared filesystem task set.

## Required Before Retrying

1. Restore reliable outbound connectivity from the `hpagent` container to the configured model
   provider(s), then verify several consecutive calls without connect timeouts.
2. Enable the local workspace tools required by the manifest (`fs_read`, `fs_write`, `fs_edit`,
   `Glob`, `Grep`, and `Bash`) for the dedicated benchmark workspace.
3. Re-run the two-trial pilot into a new JSONL file. Start the formal 60-run design only if both
   strategies succeed and each finishes within the agreed three-minute gate.

Raw runner records are retained in `agent_strategy_pilot_v2.jsonl`. This pilot must not be merged
into formal ReAct-versus-Plan success-rate claims.
