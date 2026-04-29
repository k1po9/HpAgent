# Module 4 — main.py Refactoring — Diff & Review

## Files Modified

### `src/main.py` (from 155 → 189 lines, +34)

## Changes

| Change | Lines | Description |
|--------|-------|-------------|
| Added `import sys` | 3 | For `--legacy` flag parsing |
| Added `field` import | 6 | For `dataclass` defaults |
| `AppConfig` +2 fields | 33-34 | `use_temporal: bool = True`, `temporal_host: str = "localhost:7233"` |
| `main_async(use_legacy)` | 131 | Accepts bool param, routes to mode |
| `_run_temporal_mode()` | 148-163 | New function — delegates to `temporal_worker.start_worker()` |
| `_run_legacy_mode()` | 166-179 | Extracted from old `main_async()` |
| `main()` updated | 182-184 | Parses `--legacy` flag |

## Interface Changes

```
Before                           After
─────────────────────────────────────────────────────
main_async()                     main_async(use_legacy: bool = False)
Config has 5 fields              Config has 7 fields (+use_temporal, +temporal_host)
Single startup path              Two paths: temporal (default) / legacy (--legacy)
```

## Backward Compatibility

- `--legacy` flag restores the original single-process behavior
- `AgentApplication` class untouched
- Default (`python -m src.main`) now starts Temporal mode
- All existing `config.yaml` fields unchanged; new fields have defaults

## Review

- `_run_legacy_mode` is a pure extraction — zero behavioral changes
- Temporal imports are lazy (`from temporal_worker import start_worker` inside function) so legacy mode doesn't require temporalio installed
- `agent_application.py` could optionally be extracted to a separate `legacy_main.py` in Phase 2
