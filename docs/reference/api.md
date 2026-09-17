# HTTP API Reference

The FastAPI service listens on `127.0.0.1:8080` in Compose. Health endpoints are unauthenticated:

- `GET /health/live`
- `GET /health/ready`

Primary authenticated resources under `/api/v1` are:

- account context: `GET /me`;
- QQ binding challenges: create and inspect;
- conversations: create, list, read, rename, list messages, and send a message;
- Runs: read, trace, stream events, cancel, and retry;
- files: create upload, upload content, inspect, download, delete, lineage, persistent destinations, and action approvals;
- research tasks: create, run, schedule, inspect Run/evidence/report;
- artifacts: create from a message, list, inspect, and create/read versions.

Authentication endpoints are `/auth/register`, `/auth/login`, and `/api/v1/auth/logout`. Browser clients must use the configured public origin, session cookie, and CSRF protocol. Mutation endpoints expect JSON except upload content, which expects `application/octet-stream`. Run events use `text/event-stream`.

The route declarations in `src/web_api/app.py` are the authoritative endpoint list. Contract and behavior coverage lives in `test/web_api/` and Web tests.
