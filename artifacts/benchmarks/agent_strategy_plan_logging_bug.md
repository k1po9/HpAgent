# Plan-and-Execute Pilot Bug Discovery

## Failed Pilot

- Raw record: `agent_strategy_gate_pilot_20260818T174815Z.jsonl`
- ReAct: passed `simple_001` in **26698.725 ms**.
- Plan-and-Execute: failed in **40684.815 ms** with `internal_execution_error`.
- The formal 60-run experiment was correctly not started.

## Root Cause

`DurableAgentActivities._correlation()` already supplied `plan_id`, `plan_version`, and `step_id`.
The `planning_completed`, `plan_evaluation_started`, and `plan_evaluation_completed` log calls
supplied the same keyword arguments a second time. Python raised:

```text
TypeError: common.logging.log_event() got multiple values for keyword argument 'plan_id'
```

The first planning operation had already completed before its completion log raised. Temporal then
deduplicated that operation on retry. Plan step model/tool operations also completed, but the plan
evaluation start log raised on all three Activity attempts, causing the parent Workflow to fail.

## Fix and Regression

The duplicate explicit fields were removed; the structured correlation dictionary remains the
single source for plan identity fields. A regression test now executes both a real planning path
and a plan evaluation path with non-null plan correlation values. The relevant combined suite
passed **21 tests**.

This pilot remains excluded from strategy statistics. Re-run the normal gate command after the
Worker has loaded the fix.
