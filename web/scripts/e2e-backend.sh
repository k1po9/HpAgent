#!/usr/bin/env bash
# Boot the HpAgent web API for Playwright E2E (phase-e E-07).
#
# Real API + PostgreSQL + Redis: the Fake Run Executor is enabled (test/dev
# only, never production) and streams the contract's online SSE events so the
# browser can observe the live pipeline. Credentials are provisioned at startup
# as argon2id hashes for the E2E accounts. Migrations are checksummed and
# idempotent, so a pre-existing schema is left untouched.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python"
# Local dev uses the repo venv; CI installs into the system interpreter.
if [ ! -x "${PY}" ]; then
  PY="python3"
fi

export HPAGENT_ENV=test
export APP_DATABASE_URL="${APP_DATABASE_URL:-postgresql://hpagent_api:hpagent_api@localhost:5434/hpagent}"
export WORKER_DATABASE_URL="${WORKER_DATABASE_URL:-postgresql://hpagent_worker:hpagent_worker@localhost:5434/hpagent}"
export MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL:-postgresql://hpagent_migrate:hpagent_migrate@localhost:5434/hpagent}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"

# The browser origin the API accepts for CSRF (vite dev server). Cookies are
# non-secure because E2E runs over plain http://localhost.
export WEB_PUBLIC_ORIGIN="${WEB_PUBLIC_ORIGIN:-http://localhost:5173}"
export WEB_COOKIE_SECURE=false
export WEB_CURSOR_SECRET="e2e-cursor-secret-0123456789abcdef-32bytes"
export WEB_SESSION_TOKEN_PEPPER="e2e-session-pepper-0123456789abcdef-32bytes"
export WEB_CSRF_SIGNING_KEY="e2e-csrf-signing-key-0123456789abcdef-32bytes"

# Fake executor: slow enough to click Stop and for a second tab to race the
# conversation-busy guard, fast enough to not drag the suite.
export WEB_FAKE_EXECUTOR_ENABLED=true
export WEB_FAKE_EXECUTOR_DELAY_SECONDS="${WEB_FAKE_EXECUTOR_DELAY_SECONDS:-4}"
export WEB_FAKE_EXECUTOR_MODE=success
export WEB_FAKE_EXECUTOR_CONTENT="$(cat <<'EOF'
这是由测试执行器生成的回复。

## Markdown 标题

普通段落。

- 列表一
- 列表二

**粗体文本**

`inline code`

```python
print('hello from hpagent')
```

| A | B |
|---|---|
| 1 | 2 |
EOF
)"

# Terminal notifications arrive promptly; keepalives stay quiet.
export WEB_TERMINAL_PUBLISHER_POLL_SECONDS=0.1
export WEB_SSE_KEEPALIVE_SECONDS=15

# Provision one argon2id hash shared by the E2E accounts (same password).
E2E_PASSWORD="${E2E_PASSWORD:-e2e-password}"
E2E_HASH="$("$PY" -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('${E2E_PASSWORD}'))")"
export WEB_CREDENTIALS_JSON="{\"alice\":\"${E2E_HASH}\",\"bob\":\"${E2E_HASH}\"}"

# Ensure the schema is present (no-op when already applied).
(
  cd "${REPO_ROOT}"
  PYTHONPATH=src APP_DATABASE_URL="${MIGRATION_DATABASE_URL}" "$PY" -m persistence.migrate
)

# Migrations create the schema only — logins additionally need an active 'web'
# identity binding (and an owning account) for each credential. Provision both,
# idempotently: a binding that already exists (previous run) is left untouched.
"$PY" - <<'PY'
import os
from uuid import uuid4

import psycopg

with psycopg.connect(os.environ["MIGRATION_DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        # Tables live in the `hpagent` schema (the migrations SET search_path).
        cur.execute("SET search_path TO hpagent, public")
        for subject in ("alice", "bob"):
            normalized = subject.strip().casefold()
            cur.execute(
                "SELECT 1 FROM identity_bindings "
                "WHERE provider='web' AND normalized_subject_id=%s AND status='active'",
                (normalized,),
            )
            if cur.fetchone():
                continue
            account_id, binding_id = uuid4(), uuid4()
            cur.execute(
                "INSERT INTO accounts(account_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (account_id,),
            )
            cur.execute(
                "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,"
                "external_subject_id,normalized_subject_id,verified_at) "
                "VALUES (%s,%s,'web',%s,%s,now()) ON CONFLICT DO NOTHING",
                (binding_id, account_id, subject, normalized),
            )
print("E2E identity bindings ensured: alice, bob")
PY

cd "${REPO_ROOT}"
exec env PYTHONPATH=src "$PY" -m web_api
