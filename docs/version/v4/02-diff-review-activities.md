# Module 1 — Temporal Activities — Diff & Review

## Files Created

### `src/activities/__init__.py`
Exports: `set_dependencies`, `build_context_activity`, `get_available_tools_activity`, `call_model_activity`, `execute_tool_activity`, `send_response_activity`.

### `src/activities/agent_activities.py` (+98 lines)

## Diff Summary

**New file.** Five Activities extracted from the legacy `Harness` class:

| Activity | Legacy Source | Lines |
|----------|-------------|-------|
| `build_context_activity` | `HarnessContextBuilder.build()` | wraps existing call |
| `get_available_tools_activity` | `Harness._get_available_tools()` | moved logic |
| `call_model_activity` | `Harness._call_model_internal()` → `ResourcePool.generate()` | moved logic |
| `execute_tool_activity` | `Harness.route_tool_call()` + `Sandbox.execute()` | moved logic |
| `send_response_activity` | `main.py` `AgentApplication.handle_message()` response send | new |

## Design Decisions

1. **Module-level singletons for DI**: Activities must be stateless (Temporal requirement). Dependencies (`_context_builder`, `_resource_pool`, `_sandbox_manager`, `_channel`) are set once via `set_dependencies()` at Worker boot, then read by each Activity invocation.

2. **Dict inputs/outputs**: Activity arguments are plain dicts, not dataclass instances, to avoid serialization issues across Temporal's data converter.

3. **No retry logic in Activities**: Retry is configured declaratively in the Workflow's `execute_activity()` call. Activities stay simple; Temporal handles retry/backoff.

4. **Optional channel**: If `_channel` is not injected, `send_response_activity` returns `False` gracefully — supports testing and headless operation.

## Review

- All I/O in Activities (deterministic Workflow constraint)
- No shared mutable state between invocations
- Errors bubble up for Temporal retry
- Existing `ResourcePool`, `SandboxManager`, `HarnessContextBuilder` untouched
