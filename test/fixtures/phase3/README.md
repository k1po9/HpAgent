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
