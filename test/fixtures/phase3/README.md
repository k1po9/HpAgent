# W1-B target History fixtures

Captured on 2026-09-15 from the successful isolated test namespace
`hpagent-w1b-test-e0afcea1c4`, after segmented lease / durable wait implementation.
These are new target Histories, not converted legacy fixtures.

| File | Production Workflow | Events |
| --- | --- | ---: |
| react_segments_completed.json | ReactAgentWorkflow | 68 |
| plan_segments_completed.json | PlanAndExecuteWorkflow | 113 |
| tool_approval_segments_completed.json | ToolExecutionWorkflow | 71 |

Model/tool responses are test doubles. PG lease/fencing correctness is checked
separately by the real PG + Temporal tests in `test_agent_segment_temporal.py`.
Replay verifies the production Workflow's segment/wait control history offline.
Run `.venv/bin/pytest -q test/test_agent_segment_replay.py` from the repository root.
Do not regenerate these fixtures merely to hide a determinism regression.

## W1-C lifecycle histories

`lifecycle_react_completed.json` and `lifecycle_plan_and_execute_completed.json`
were captured from canonical `AgentLifecycleWorkflow` in isolated Temporal namespace
`hpagent-w1b-test-33ff442496` (2026-09-15). The PG Command/Outbox/lifecycle, context,
transcript, segment and terminal commits are real; model/action/Sandbox services
use controlled test substitutes. These are newly captured target histories, not
converted legacy fixtures. Both are replayed offline in `test_agent_segment_replay.py`.
Reproduce into a new directory with `scripts/capture_web_workflow_history.py`.
