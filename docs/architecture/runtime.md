# Runtime

## Command admission

Web and QQ adapters translate requests into application commands. `CommandService` opens a PostgreSQL transaction that records messages, session/run state, idempotency facts, and an Outbox event atomically. A request is admitted only when that transaction commits.

## Dispatch and lifecycle

The Outbox dispatcher leases pending events and starts `AgentLifecycleWorkflow` on `hpagent-web-lifecycle`. The lifecycle workflow prepares the Run, records its Temporal identity, starts `AgentRunWorkflow` on `hpagent-web-agent`, and finalizes success, failure, or cancellation.

`AgentRunWorkflow` selects the requested execution strategy:

- **ReAct** alternates model decisions and tool execution.
- **Plan-and-Execute** builds a plan and executes durable steps.

Child workflows and activities carry stable Run and operation identities. Activities perform all nondeterministic I/O: database access, model calls, tools, workspace changes, memory, research discovery, artifact building, and document normalization.

## Shared runtime

The main worker composition owns one shared resource pool, workspace isolation runtime, sandbox manager, Brain, Action runtime, Redis client, optional MCP manager, and Hindsight-backed memory collaborators. `Context` assembles conversation state, memory recall, files, workspace facts, prompts, and the source interaction profile for the Brain.

`Brain` owns model-facing decisions. `Actions` selects and invokes local tools, MCP tools, and sandbox operations. Mutable tool-selection state is scoped by resource and execution identity.

## Independent durable capabilities

Research and artifact workflows are registered with the durable runtime but remain application capabilities rather than agent strategies. Heavy document normalization is executed by the dedicated `hpagent-document-worker` process on `hpagent-document` so expensive conversion cannot consume the main agent activity capacity.

See [Temporal Reference](../reference/temporal.md) for queues and workflow names.
