# Phase 3 W3-B · Delete Legacy Runtime

Baseline: `378ca723b977426b2045e293aecd622cef337e87` (W3-A complete).

## Result

W3-B deleted 44 legacy source modules after recomputing reachability from the current tree and
capturing the actual production Temporal registries. The deleted roots are:

- `agent_execution` including `QQExecutionHost`, `WebExecutionHost`, `AgentExecutionFacade`,
  `DefaultBrainActionLoop`, old channel adapters and W3-A forwarding shims;
- `harness` forwarding shims;
- `orchestration.workflow`, `orchestration.web_workflow`,
  `orchestration.web_activities` and `orchestration.scheduler`;
- the unregistered experimental `agent` package.

The corresponding old tests and the `WebRunWorkflow` History fixture were retired. Tests that
still expressed canonical behavior were ported to the owning Activity, context assembly, trace,
reply and lifecycle contracts. `W3_B_retired_test_contracts.json` records each removed test and its
replacement or retirement reason.

No phase2_2 evidence was changed. The user's existing phase2_1 truth-table edit remains excluded
from this work. QQ SessionStore/WAL, SQLite workspace metadata and archive helpers remain for the
later state-authority phase. Research, Document, Artifact, Reflection and Metrics workflows remain.

## Production reachability

The post-delete composition registry contains:

| Task queue | Workflows | Activities |
|---|---:|---:|
| `hpagent-web-lifecycle` | 5 | 22 |
| `hpagent-web-agent` | 5 | 11 |
| `hpagent-document` | 0 | 1 |
| `hpagent-task-queue` | 2 | 3 |

Required independent workflows include Research report/schedule, Document normalization, Artifact
build, Reflection and Metrics. No retired Workflow, Activity or runtime type is registered.

Both product surfaces now have one production chain:

```text
Web / QQ ingress
  -> Conversation Command
  -> PostgreSQL Run + Outbox
  -> WebOutboxDispatcher
  -> AgentLifecycleWorkflow
  -> AgentRunWorkflow
  -> segmented durable Activities
```

The static scan reports 44 deleted modules, zero remaining retired modules, zero retired imports,
zero retired canonical reachability, zero retired definitions and zero missing required workflows.

## Validation

| Check | Result | Evidence |
|---|---|---|
| Focused runtime/ownership/replay contracts | **92 passed** | `W3_B_focused_validation.txt` |
| Full W2 + W3-B integration run | **187 passed, 2 failed** | `W3_B_integration_validation.txt` |
| Failed-test isolated rerun | Activity worker SIGKILL **passed**; concurrent first QQ admission failed | `W3_B_integration_failure_rerun.txt` |
| W3-B integration scope, excluding reproduced state-authority race | **698 passed, 1 deselected** | `W3_B_integration_scope_validation.txt` |
| Production registry/reachability scan | **PASS** | `W3_B_registry_validation.txt`, `W3_B_runtime_scan.json` |
| Test collection | **699 collected** | `W3_B_collection_validation.txt` |

The remaining integration failure is
`test_distinct_first_deliveries_share_binding_and_pg_admission`: two concurrent first QQ deliveries
race on `uq_conversations__account_conversation`. It reproduced in isolation and is in PostgreSQL
conversation admission/state authority, which W3-B did not modify. Fixing that race belongs to the
separate state-authority work and is intentionally not folded into runtime deletion. The initial
Activity worker SIGKILL timeout passed on isolated rerun; its recovery and side-effect assertion is
therefore retained as passing replay/recovery evidence.

Final Ruff, diff, phase2_2 integrity, registry and source-removal results are recorded in
`W3_B_static_validation.txt`; exact commands are in `W3_B_validation_commands.txt`.

## Gate

The W3-B retirement gate passes: canonical consumers are redirected, the legacy runtime is absent
from source and registries, and independent workflows remain registered. The concurrent QQ
admission race is an explicit state-authority finding for the next applicable phase.
