# W4-B — Composition / Protocol Consolidation Implementation Report

## 1. Result and W4-A plan followed

W4-B implemented the bounded structural changes approved by
`W4_A_composition_ownership_audit.md`. The implementation keeps the single canonical durable
Agent path and does not change Conversation admission, lifecycle state transitions, session
rotation, Outbox, Temporal retry/fencing, Agent strategies, approvals, Research, Artifact,
Document, Workspace Query, or Model Input Review semantics.

This report does **not** claim W4/G02 closed. Broad closure validation remains W4-C work.

The pre-existing worktree modification to
`artifacts/architecture-audit/phase2_1/03_architecture_truth_table.md` was left untouched.

## 2. Files and symbols changed

### Shared composition and ownership

- Added `bootstrap.shared_runtime.SharedAgentServices` and
  `build_shared_agent_services` as the owner of the one shared `BrainEngine`, `ActionRuntime`,
  context builder, account service, Hindsight reference, reflection service, and metrics service.
- Replaced `bootstrap.qq.build_qq_runtime` / `QQRuntimeServices` with the surface-only
  `build_qq_surface_services` / `QQSurfaceServices`. The QQ builder now owns only the channel
  router and reply adapter.
- Replaced `orchestration.worker.WebWorkerComposition` and `compose_web_workers` with the
  surface-neutral `DurableRuntimeComposition` and `compose_durable_runtime`.
- Split the worker dependency result into typed `SharedInfrastructure`, `SharedAgentServices`,
  and `QQSurfaceServices` groups under `WorkerDependencies`.

### Capability protocols and Action cleanup

- Added `actions.contracts.ActionCapability` and `brain.contracts.BrainCapability` beside the
  abstractions they describe; `DurableAgentActivities` now consumes those protocols.
- `ActionRuntime` now requires `resource_key` and `execution_id` for selection and execution,
  keys mutable selection state by `(resource_key, execution_id)`, and exposes
  `reset_execution` / `clear_execution` only.
- Removed the obsolete optional execution identity, session-only cache fallback,
  `reset_turn`, `clear_session`, public raw `execute` surface, and legacy Host/QQ commentary.
- Preserved tool selection, result shaping/summarization, idempotency injection, invocation keys,
  side-effect classification, budget reservations, and Sandbox routing.

### Interaction-profile boundary

- Added `application.interaction_profiles` for the canonical semantic profiles
  `web_chat`, `web_plan`, `qq_private`, `qq_group`, and `console`.
- QQ normalization now persists the selected semantic profile in origin metadata.
- `HarnessContextBuilder` consumes only `interaction_profile`; it no longer imports
  `ChannelType`, detects a transport from event content, or maps a profile back to a transport.
- Context events carry source/profile facts in metadata rather than embedding `channel_type` in
  model-visible message content. Cross-source prompting reads `interaction_source` metadata.
- Hindsight recall no longer receives the unused transport-only `channel_type` argument; account,
  session, private/group scope, and group identity are unchanged.

### Explicit instance ownership and resource lifetime

- Replaced module-global Activity injection with instance-owned
  `RunLifecycleActivities`, `ArtifactActivities`, and `ScheduledMemoryActivities`. Explicit
  Temporal Activity names preserve the existing wire contract.
- Removed the global reminder scheduler and pass the scheduler into reminder factories through
  `SandboxManager` construction.
- Added `BackgroundTasks`, which owns, cancels, and awaits every worker background loop as a set.
- Added `SandboxManager.close()` to destroy all process-owned Sandboxes and clear runtime bindings.
- Worker dependency construction now uses an `AsyncExitStack`, registering workspace isolation,
  Redis, MCP, and Sandbox cleanup immediately after acquisition. Partial MCP startup is also
  disconnected locally.
- `WorkerDependencies.close()` owns the acquired resource stack; worker shutdown no longer
  repeats individual optional-local cleanup branches.
- Web API lifespan now uses an `AsyncExitStack` from its first PostgreSQL pool acquisition and
  immediately registers cleanup for API/worker pools, Redis, terminal publisher, and fake
  executors. Startup failures therefore unwind already-acquired resources.

## 3. Ownership before → after

| Concern | Before | After |
| --- | --- | --- |
| Shared Brain/Actions/Memory | Constructed by QQ-named bootstrap | Constructed once by `build_shared_agent_services` |
| QQ composition | Shared Agent plus QQ presentation mixed together | Router/reply presentation only |
| Durable worker composition | Web-named despite serving Web and QQ | `DurableRuntimeComposition` consuming shared capabilities |
| Activity dependencies | Process-global mutable injection | Bound methods on explicitly constructed Activity collections |
| Reminder scheduler | Module-global late injection | Constructor/factory dependency |
| Background loops | Nullable task locals and positional tuples | One root-owned `BackgroundTasks` set |
| Worker external resources | Cleanup spread across locals; startup gaps | One transferred `AsyncExitStack` owner |
| API lifespan resources | `try/finally` began after several acquisitions | Cleanup registered at each successful acquisition |

No second Agent runtime, Brain, Action runtime, ResourcePool, Sandbox manager, or workspace lock
registry was introduced.

## 4. Protocol before → after

| Boundary | Before | After |
| --- | --- | --- |
| Durable Activities → Actions | Broad `Any`; optional session/execution call shape | `ActionCapability`; mandatory resource + execution identity |
| Durable Activities → Brain | Broad `Any` | `BrainCapability` with only the consumed model operations |
| Context behavior | Context builder inferred concrete transport | Surface/source selects semantic interaction profile |
| Temporal Activities | Module functions lookup injected globals | Named bound Activity methods own dependencies |

Protocols remain in `actions.contracts` and `brain.contracts`; no generic interfaces package was
created.

## 5. Removed wrappers and compatibility residue

Removed production symbols and paths:

- `build_qq_runtime`, `QQRuntimeServices`
- `compose_web_workers`, `WebWorkerComposition`
- `inject_run_lifecycle`, `inject_agent_run_loader`, `inject_agent_event_factory`
- `inject_artifact_build_service`, `inject_scheduled_services`, reminder `inject_scheduler`
- Action `reset_turn`, `clear_session`, optional execution identity, and session-only cache keys
- context-builder transport detection and channel override compatibility mapping

One intentionally bounded compatibility read remains: `select_interaction_profile` maps legacy
persisted origin records that predate the new `interaction_profile` field. New QQ ingress writes
the explicit profile. Retaining this read prevents prompt/personality changes for already-persisted
Runs; it is isolated outside `HarnessContextBuilder` and does not restore a transport-owned Agent
path.

## 6. Resource lifecycle evidence

- Worker startup failure unwinds callbacks registered during dependency construction.
- Normal worker teardown stops channel monitors, cancels/awaits the owned background task set, and
  closes the dependency resource stack.
- The stack closes all Sandboxes, MCP, Redis, and the workspace process lock in reverse acquisition
  order.
- Redis degradation clears the unusable client/cache references while preserving registered
  cleanup for any partially created client.
- API startup failure after opening the API pool closes that pool; normal lifespan teardown also
  preserves the former fake-artifact → fake-runner → worker-pool and publisher → Redis ordering.

## 7. Validation actually executed

1. Focused regression selection covering ActionRuntime, durable Agent behavior, worker
   composition/startup, context/profile behavior, Web configuration, QQ protocol/identity/delivery,
   background loops, Activity ownership, and resource cleanup:
   `129 passed, 1 third-party deprecation warning`.
2. Full suite collection: `689 tests collected` with no collection/import error. Integration tests
   requiring PostgreSQL/Temporal were collected but not executed in W4-B.
3. Earlier focused ownership/background/resource reruns: `94 passed`, `18 passed`, and final
   resource-gate rerun `6 passed`; these overlap the 129-test selection. After enforcing non-empty
   execution identity at the Action boundary, the affected Action/durable subset was rerun:
   `54 passed`.
4. `ruff check` on all changed Python production/test files: passed.
5. `python -m compileall -q src test`: passed.
6. `git diff --check`: passed.
7. Static searches confirmed:
   - only `bootstrap/shared_runtime.py` constructs `BrainEngine` and `ActionRuntime`;
   - no old QQ/shared builder, Web-named composition, global injection API, `reset_turn`, or
     `clear_session` remains in `src` or `test`;
   - shared Action/Brain/Memory/Application code has no bootstrap or concrete QQ channel import;
   - the QQ builder contains no shared Agent/runtime construction.

## 8. Deviations from W4-A

No target ownership decision was redesigned. The only bounded implementation qualification is the
legacy persisted-origin profile fallback described above. Removing it would change prompt behavior
for pre-W4 data, contrary to the behavior-preservation constraint.

The deferred W4-A items remain deferred: historical class/package names such as
`HarnessContextBuilder`, unrelated broad collaborator `Any` annotations, the Sandbox surface fact,
and worker PostgreSQL pooling were not expanded into W4-B work.

## 9. Remaining W4-C validation items

- Execute the PostgreSQL/Temporal integration selections for real Web and QQ durable execution,
  lifecycle Activities, Artifact workflow, and source-owned runs in the closure environment.
- Exercise worker/API startup-failure and graceful-shutdown paths against real Redis, MCP, pools,
  channel monitors, and Temporal workers.
- Run the broad clean-image/build validation and repeat architecture/static gates from a clean
  worktree context.
- Decide closure status for W4/G02 from those results; this report does not pre-judge it.

## 10. Scope confirmation

W5 snapshot/review work, W6 query endpoints, and W7 capability restructuring were not entered.
