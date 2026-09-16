# Runtime Refactor Closure Report

## Phase 3 W3-B result

W3-B removed the Agent runtime roots that no longer participate in the production composition:
the old Web Workflow and whole-turn Activity, the old QQ turn Workflow/Activity, both channel
execution Hosts, `AgentExecutionFacade`, `DefaultBrainActionLoop`, duplicate orchestration wiring,
and the unregistered experimental Multi-Agent package.

The production execution path is now:

```text
Web / QQ ingress
  -> Conversation Command
  -> PostgreSQL Run + Outbox
  -> WebOutboxDispatcher
  -> AgentLifecycleWorkflow
  -> AgentRunWorkflow
  -> segmented durable Activities
```

Research, Document and Artifact workflows remain registered independent capabilities. Reflection
and metrics schedules remain on `hpagent-task-queue`; they do not provide an alternate Agent loop.

W3-A moved the live DTO, protocol, trace, budget, context, archive/reflection and scheduling
survivors to their owning capabilities before these modules were removed. The W3-B registry scan
captures every production task queue at the composition boundary and rejects retired Workflow,
Activity and runtime type names.

QQ SessionStore/WAL, SQLite workspace metadata and other historical state authorities are outside
this runtime-deletion step and remain available for the separate state-authority review.

Current evidence is stored under `artifacts/architecture-audit/phase3/W3_B_*`.
