# Module 2 — AgentWorkflow — Diff & Review

## Files Created

### `src/workflows/__init__.py`
Exports: `AgentWorkflow`.

### `src/workflows/agent_workflow.py` (+114 lines)

## Diff Summary

**New file.** Temporal Workflow that replaces the combined logic of:

| Legacy Component | Replaced By |
|-----------------|-------------|
| `Orchestrator.receive_request()` | `run()` entrypoint (receives user_message dict) |
| `Orchestrator.process_session()` | Main `while` loop (max_turns guard) |
| `Harness.wake()` | Loop body: build_context → get_tools → call_model → route_tools |
| `Orchestrator._get_or_create_session()` | Deferred to caller; Workflow is per-session |

## Key Design Decisions

1. **Per-session Workflow instance**: Each user message starts (or signals) an `AgentWorkflow`. The Workflow ID = session ID, giving Temporal-native session lifecycle management.

2. **Events stored as `List[Dict]`**: Same data shape as legacy `Event.to_dict()` for compatibility. Temporal persists this list automatically via event sourcing — no file I/O needed.

3. **Retry policies inline**: `call_model_activity` gets 3 attempts with exponential backoff; `execute_tool_activity` gets 2 attempts. Temporal handles the retry loop transparently.

4. **Signals + Queries**: `cancel_session` signal allows external cancellation; `get_events` / `get_status` queries replace `SessionManager.get_events()` / `Orchestrator.get_task_status()`.

5. **No Continue-As-New yet**: Deferred to Phase 2 per the temporal.md plan. Current 20-turn max keeps event history well under Temporal's size limits.

## Interface Mapping

```
Legacy call site                          →  Temporal call site
────────────────────────────────────────────────────────────────
orchestrator.receive_request(msg)        →  client.start_workflow(AgentWorkflow.run, msg)
orchestrator.process_session(sid)        →  (runs inside workflow)
session_manager.get_events(sid)          →  workflow.query(get_events)
orchestrator.get_task_status(tid)        →  workflow.query(get_status)
orchestrator.cancel_task(sid)            →  workflow.signal(cancel_session)
```

## Review

- Deterministic: only `workflow.now()`, `workflow.random()`, and `workflow.execute_activity()` inside the workflow
- All I/O delegated to Activities
- Event history grows linearly with turns; max 20 turns × ~5 events = ~100 events per execution
