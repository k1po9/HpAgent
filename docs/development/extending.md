# Extending HpAgent

## Add a tool

Implement the tool under the appropriate `src/sandbox/tools/` adapter, declare its schema and side-effect/idempotency metadata, register it in the tool registry, and add focused contract tests. Any write must use the current execution identity and respect workspace, file, approval, budget, and fencing boundaries.

## Add an MCP capability

Declare the server in `config/mcp/servers.yaml`, keep credentials in environment variables, and validate discovery with `python scripts/check/mcp-health.py`. MCP adapters must present the same Action result and side-effect semantics as local tools.

## Add an application capability

Place domain rules in a domain package, coordination in application services, and provider/storage details in adapters. Admit durable work through PostgreSQL plus Outbox when callers need transactional acceptance. Web and QQ should remain presentation surfaces over the shared command boundary.

## Add a Temporal workflow or activity

- Keep workflow code deterministic and move I/O into activities.
- Use stable workflow, Run, and operation identifiers.
- Define retry and timeout behavior deliberately; classify permanent errors.
- Persist idempotency and side-effect facts before external writes.
- Register the workflow/activity on its owning queue and test registry presence.
- Cover cancellation, replay compatibility, restart recovery, lease reacquisition, and stale fencing where applicable.
