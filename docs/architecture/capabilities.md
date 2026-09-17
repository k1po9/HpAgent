# Capabilities

## Agent

The Agent capability combines context assembly, a Brain/model decision loop, and durable strategy workflows. ReAct and Plan-and-Execute share the same lifecycle, Actions, memory, workspace, and committed result path.

## Actions, Tools, MCP, and Sandbox

Actions expose a common invocation contract over local tools and configured MCP servers. Tool retrieval selects a bounded relevant set. Side-effect metadata, stable operation identifiers, budgets, and reconciliation protect retryable execution. Shell-like execution can use the sandbox and account-scoped workspace isolation.

## Memory

Context assembly recalls Hindsight memory before model execution. Completed Runs are retained asynchronously with account, conversation, Run, and source metadata. Scheduled reflection and metrics workflows operate through the same memory boundary.

## File and Workspace

The File capability owns upload, validation, content access, Run bindings, persistent destinations, lineage, approvals, and generated outputs. Workspace supplies an account-scoped Git repository and Run-scoped execution directories. Metadata remains in PostgreSQL; blobs and worktrees remain in their dedicated storage.

## Research

Research is a fixed workflow that plans queries, discovers sources through SearXNG, acquires and extracts content, creates evidence, synthesizes claims, and publishes a report. It is not an Agent strategy and does not replace ReAct or Plan-and-Execute.

## Artifact

Artifacts are versioned application outputs associated with source messages or research. Artifact build work is dispatched durably and updates PostgreSQL state through activities.

## Heavy Document

Heavy Document normalization is the expensive branch of the File capability. A dedicated Temporal activity worker converts and normalizes supported documents, records idempotent operation state, accounts for budget, and publishes normalized output. It is not a second file system.
