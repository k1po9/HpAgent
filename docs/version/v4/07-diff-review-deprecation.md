# Modules 6 & 7 — Deprecation Markers — Diff & Review

## Files Modified

### `src/orchestration/orchestrator.py` (+6 lines module doc, +5 lines __init__)

| Change | Description |
|--------|-------------|
| Module docstring | Documents replacement: AgentWorkflow + Activities + TemporalSessionManager |
| `from __future__ import annotations` | Future-proof imports |
| `import warnings` | For DeprecationWarning |
| `Orchestrator.__init__()` +5 lines | `warnings.warn("Orchestrator is deprecated...", DeprecationWarning, stacklevel=2)` |

### `src/harness/harness.py` (+6 lines module doc, +5 lines __init__)

| Change | Description |
|--------|-------------|
| Module docstring | Documents replacement: call_model_activity, execute_tool_activity, build_context_activity |
| `from __future__ import annotations` | Future-proof imports |
| `import warnings` | For DeprecationWarning |
| `Harness.__init__()` +5 lines | `warnings.warn("Harness is deprecated...", DeprecationWarning, stacklevel=2)` |

### `src/orchestration/retry_policy.py` (+6 lines module doc, +5 lines __init__)

| Change | Description |
|--------|-------------|
| Module docstring | Documents replacement: Temporal's native RetryPolicy per-activity |
| `from __future__ import annotations` | Future-proof imports |
| `import warnings` | For DeprecationWarning |
| `RetryExecutor.__init__()` +5 lines | `warnings.warn("RetryExecutor is deprecated...", DeprecationWarning, stacklevel=2)` |

## Review

- Zero behavioral changes — all classes function identically to before
- `DeprecationWarning` only fires on instantiation (not on import)
- `stacklevel=2` ensures the warning points to the caller's code, not the `__init__`
- Legacy mode (`--legacy`) suppresses deprecation warnings by default (Python default behavior)
- No code removed — full backward compatibility
