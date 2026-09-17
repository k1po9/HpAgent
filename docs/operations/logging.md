# Logging

## Compose logs

Use the repository entry point instead of remembering profile flags:

```bash
./scripts/operations/logs.sh                         # follow all services
./scripts/operations/logs.sh --api                   # FastAPI
./scripts/operations/logs.sh --worker                # main + document workers
./scripts/operations/logs.sh --qq                    # worker + NapCat
./scripts/operations/logs.sh --infra                 # data/durability services
./scripts/operations/logs.sh --api -n 100 --no-follow
./scripts/operations/logs.sh temporal redis -n 200
```

Direct Compose equivalents remain valid: `docker compose --profile web logs -f --tail 200 hpagent` and `docker compose --profile web logs -f --tail 200 hpagent-api`.

## Structured application logs

The main worker writes `.data/logs/hpagent.jsonl` and `.data/logs/hpagent-error.log`. The API writes `.data/logs/web-api.jsonl` and `.data/logs/web-api-error.log`. JSONL records include `ts`, `level`, `logger`, `msg`, and, for lifecycle events, `event`, `component`, and correlation fields.

```bash
jq 'select(.run_id == "RUN_UUID")' .data/logs/*.jsonl
jq 'select(.conversation_id == "CONVERSATION_UUID")' .data/logs/*.jsonl
jq 'select(.workflow_id == "WORKFLOW_ID")' .data/logs/*.jsonl
jq 'select(.operation_id == "OPERATION_ID")' .data/logs/*.jsonl
jq 'select(.level == "ERROR")' .data/logs/*.jsonl
```

When only container output is available:

```bash
./scripts/operations/logs.sh --worker --no-follow | grep -F 'RUN_UUID'
./scripts/operations/logs.sh --qq --no-follow | grep -F 'MESSAGE_OR_DELIVERY_ID'
```

## Trace a Run

1. Start with `run_id` from the API response, SSE event, or QQ delivery row.
2. Find `conversation_id`, execution events, and `workflow_id` in JSONL or `GET /api/v1/runs/{run_id}` and `/trace`.
3. Follow `operation_id` for tool, file, research, or document side effects.
4. Check `lease_token`/fencing-related events when a Run resumes or a worker restarts.

QQ ingress/delivery records may also carry provider message identity, `delivery_id`, and `msg_seq`. Search the normalized provider message identifier first, then pivot to `run_id`.

## Execution failure versus delivery failure

An execution failure leaves the Run failed and appears in lifecycle/agent events. A delivery failure occurs after a result is committed: the Run may be completed while `qq_deliveries` is `failed` or `uncertain`. Inspect worker logs for delivery events and PostgreSQL delivery state; retrying delivery must not start a new Run.

`LOG_LEVEL` controls console verbosity. JSONL retains DEBUG and above and rotates daily for 30 files; Compose also rotates selected container JSON logs by size.
