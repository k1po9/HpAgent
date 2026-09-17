# HpAgent Documentation

`docs/` describes the current system. Historical implementation plans and closure evidence are not part of current documentation; repository history and `artifacts/` retain evidence when needed.

## Architecture

- [Overview](architecture/overview.md) — context, layers, surfaces, and the canonical execution flow.
- [Runtime](architecture/runtime.md) — command admission, dispatch, workflows, strategies, and activities.
- [Data and State](architecture/data-and-state.md) — state ownership and persistence boundaries.
- [Capabilities](architecture/capabilities.md) — agent, tools, memory, files, research, artifacts, and documents.
- [Reliability](architecture/reliability.md) — durability, retries, leases, fencing, idempotency, and cancellation.
- [Sequences](architecture/sequences.md) — the important end-to-end paths.

## Development

- [Setup](development/setup.md)
- [Testing](development/testing.md)
- [Extending HpAgent](development/extending.md)

## Operations

- [Deployment](operations/deployment.md)
- [Runbook](operations/runbook.md)
- [Logging](operations/logging.md)
- [Troubleshooting](operations/troubleshooting.md)
- [Backup and Restore](operations/backup-restore.md)

## Reference

- [Configuration](reference/configuration.md)
- [Repository Layout](reference/repository-layout.md)
- [Temporal](reference/temporal.md)
- [HTTP API](reference/api.md)
