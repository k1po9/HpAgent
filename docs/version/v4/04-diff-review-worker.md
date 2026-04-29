# Module 3 — Temporal Worker — Diff & Review

## Files Created

### `src/temporal_worker.py` (+131 lines)

## Diff Summary

**New file.** Replaces the orchestration startup logic scattered across `main.py`'s `AgentApplication.initialize_async()` and `main_async()`.

Two public async functions:

| Function | Purpose |
|----------|---------|
| `init_dependencies(config)` | Create ResourcePool, SandboxManager, HarnessContextBuilder. Returns tuple for DI. |
| `start_worker(config)` | Full boot: init deps → inject into Activities → connect Temporal → start Worker + channel listeners |

## Key Design Decisions

1. **`set_dependencies()` called twice**: Once with `channel=None` (for early Activity registration), then again with `channel=napcat` (for `send_response_activity`). This is intentional — the channel must exist before injection.

2. **Workflow ID = `hpagent-{sender_id}`**: Each sender gets one long-running Workflow. New messages from the same sender can signal the existing Workflow (future: multi-turn conversations within one Workflow instance).

3. **Single task queue**: `"hpagent-task-queue"` for both Workflows and Activities — simplest setup for Phase 1. Phase 2 can split into separate queues for scaling.

4. **Worker + channel concurrent via `async with worker:`**: The Temporal Worker and the NapCat WebSocket server run in the same event loop. The Worker context manager handles graceful shutdown.

## Review

- Dependency initialization follows the same order as legacy `AgentApplication.initialize_async()`
- Channel callback `handle_napcat_message` mirrors legacy `AgentApplication.handle_message()` but calls Temporal client instead of Orchestrator
- No changes to existing files — pure addition
