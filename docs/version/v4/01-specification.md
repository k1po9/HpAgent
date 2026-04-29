# HpAgent Temporal Refactoring — Specification of Changes

---

## 1. Files to Create

### 1.1 `src/workflows/__init__.py`
Empty init, exports `AgentWorkflow`.

### 1.2 `src/workflows/agent_workflow.py`
**Interface: `AgentWorkflow`** (Temporal Workflow)

```
@workflow.defn
class AgentWorkflow:
    def __init__(self):
        self._events: List[Dict[str, Any]]
        self._max_turns: int
        self._turn_count: int
        self._completed: bool

    @workflow.run
    async def run(self, user_message: Dict[str, Any]) -> Dict[str, Any]

    @workflow.query
    def get_events(self) -> List[Dict[str, Any]]

    @workflow.query
    def get_status(self) -> Dict[str, Any]

    @workflow.signal
    async def cancel_session(self)
```

Replaces: `Orchestrator.receive_request()` + `Orchestrator.process_session()` + `Harness.wake()` loop logic.

### 1.3 `src/activities/__init__.py`
Empty init, exports all activity functions + `set_dependencies`.

### 1.4 `src/activities/agent_activities.py`
**Interface: Activity functions + dependency injection**

```python
# Dependency injection (called at Worker startup)
def set_dependencies(ctx_builder, res_pool, sandbox_mgr, channel=None)

# Activities
@activity.defn
async def build_context_activity(events, channel_type) -> List[Dict]

@activity.defn
async def get_available_tools_activity() -> List[Dict]

@activity.defn
async def call_model_activity(context, tools) -> Dict

@activity.defn
async def execute_tool_activity(tool_name, arguments) -> Dict

@activity.defn
async def send_response_activity(content, user_message) -> bool
```

Replaces: `Harness._call_model_internal()`, `Harness._get_available_tools()`, `Harness.route_tool_call()`, `HarnessContextBuilder.build()` (wrapped).

### 1.5 `src/temporal_worker.py`
**Interface: Temporal Worker entrypoint**

```python
async def init_dependencies(config) -> tuple  # resource_pool, sandbox_manager, context_builder
async def start_worker(config) -> None          # start Temporal Worker + channel listeners
```

New file. Replaces the orchestration startup logic in `main.py`.

---

## 2. Files to Modify

### 2.1 `src/main.py`
**Changes:**
- Add `use_temporal: bool = True` to `AppConfig`
- Add `run_temporal_mode()` path in `AgentApplication`
- Keep legacy `run_legacy_mode()` as fallback
- Wire channel callbacks to `execute_workflow` instead of `Orchestrator.receive_request`

**Modified interfaces:**
- `AppConfig` — add `use_temporal` field
- `AgentApplication.initialize_async()` — add Temporal branch
- `AgentApplication.handle_message()` — delegate to Temporal client when enabled
- `main_async()` — add `--legacy` CLI flag

### 2.2 `src/session/session_manager.py`
**Changes:**
- Mark as `@deprecated` — Temporal Event History replaces file persistence
- Keep `SessionManager` for backward compat, add `TemporalSessionManager` subclass
- `TemporalSessionManager` delegates to Workflow queries instead of file repo

**Modified interfaces:**
- New class `TemporalSessionManager(ISession)` — reads events via Temporal Query
- `SessionManager` — add deprecation warning

### 2.3 `src/orchestration/orchestrator.py`
**Changes:**
- Add module-level deprecation warning
- All methods remain but delegate to Workflow client when Temporal mode active

**Modified interfaces:**
- `Orchestrator.__init__()` — accept optional `temporal_client` param

### 2.4 `src/harness/harness.py`
**Changes:**
- Add module-level deprecation warning  
- `Harness` kept as legacy adapter, Activities replace its core logic

**Modified interfaces:**
- No interface changes; mark class as deprecated

---

## 3. Files to Deprecate (no code removal)

### 3.1 `src/orchestration/retry_policy.py`
All retry logic replaced by Temporal's native `RetryPolicy`. File kept with deprecation notice.

### 3.2 `src/session/repositories.py`
`FileSessionRepository` / `FileEventRepository` — file-based persistence replaced by Temporal Event History. Kept for backward compat.

---

## 4. Files Unchanged

| File | Reason |
|------|--------|
| `src/common/types.py` | Core data types reused as-is |
| `src/common/errors.py` | Error hierarchy unchanged |
| `src/common/interfaces.py` | ISession, IResources, ISandbox, IChannel kept; IHarness/IOrchestration deprecated |
| `src/resources/resource_pool.py` | Injected into Activities, no changes needed |
| `src/resources/credentials.py` | No changes |
| `src/model/client.py` | No changes |
| `src/sandbox/sandbox.py` | No changes |
| `src/sandbox/sandbox_manager.py` | No changes |
| `src/sandbox/tools/base.py` | No changes |
| `src/sandbox/tools/factory.py` | No changes |
| `src/sandbox/tools/registry.py` | No changes |
| `src/sandbox/channels/base.py` | No changes |
| `src/sandbox/channels/napcat.py` | No changes (callback signature compatible) |
| `src/sandbox/channels/console.py` | No changes |
| `src/harness/context_builder.py` | Injected into Activities, no changes needed |
| `src/session/models.py` | Data classes kept for typing |
| `src/migration/legacy_converter.py` | No changes |
| `src/migration/migration_runner.py` | No changes |

---

## 5. Dependency Additions

```
temporalio >= 1.7.0
```

Added to `requirements.txt` or `pyproject.toml`.

---

## 6. Module Completion Order

1. **Module 1**: `src/activities/` — Activities (independent, depends on existing resources/sandbox/harness)
2. **Module 2**: `src/workflows/` — AgentWorkflow (depends on activities)
3. **Module 3**: `src/temporal_worker.py` — Worker entrypoint (depends on workflow + activities)
4. **Module 4**: `src/main.py` — Application wiring (depends on worker)
5. **Module 5**: `src/session/` — TemporalSessionManager (depends on workflow queries)
6. **Module 6**: `src/orchestration/` + `src/harness/` — Deprecation markers
7. **Module 7**: `src/orchestration/retry_policy.py` — Deprecation marker

Each module completion produces a `diff-review-XX-module-name.md` at `docs/version/v4/`.
