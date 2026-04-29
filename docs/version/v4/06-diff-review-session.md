# Module 5 — Session Layer Simplification — Diff & Review

## Files Modified

### `src/session/session_manager.py` (+88 lines)

## Changes

| Change | Lines | Description |
|--------|-------|-------------|
| Module docstring updated | 1-5 | Added Temporal deprecation notice |
| New class `TemporalSessionManager(ISession)` | +82 | ISession impl backed by Temporal Queries |

## New Class: `TemporalSessionManager`

Implements `ISession` so it's a drop-in replacement wherever that interface is used.

```
class TemporalSessionManager(ISession):
    __init__(self, temporal_client=None)

    # Read operations → Temporal Queries
    async get_events(session_id, ...) → List[Event]    # queries get_events on workflow
    async list_sessions(limit, offset, ...) → List[SessionMetadata]  # local cache

    # Write operations → no-ops (Temporal owns state)
    async create_session(metadata) → str               # tracks locally
    async emit_event(event) → str                       # returns event_id
    async rewind_session(...) → Dict                    # returns empty result
    async archive_session(...) → bool                   # marks local cache
```

## Key Design Decisions

1. **Reads go to Temporal Queries**: `get_events()` calls `workflow.query("get_events")` via the Temporal client, returning live event history without touching files.

2. **Writes are no-ops**: `emit_event`, `rewind_session`, `archive_session` return success without side effects. Temporal's event sourcing automatically records all Activity inputs/outputs — manual event persistence is redundant.

3. **Local session cache**: `_sessions` dict tracks session metadata locally. Not needed for correctness (Temporal is the source of truth) but useful for `list_sessions()` without hitting the Temporal Server.

4. **Graceful degradation**: If `temporal_client` is None or the workflow handle doesn't exist, `get_events()` returns `[]` instead of raising.

## Backward Compatibility

- `FileSessionRepository` and `FileEventRepository` unchanged
- `SessionManager` unchanged (legacy mode still works)
- `session/models.py` unchanged (data classes shared by both managers)

## Review

- `ISession` interface fully implemented — drop-in for existing code expecting that contract
- Event reconstruction (`EventRecord → Event`) mirrors legacy `SessionManager.get_events()` format
- No file I/O in read path — purely in-memory + Temporal gRPC
