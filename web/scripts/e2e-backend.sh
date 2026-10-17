#!/usr/bin/env bash
# Boot the HpAgent web API for Playwright E2E.
#
# Real API + PostgreSQL + Redis: the Fake Run Executor is enabled (test/dev
# only, never production) and streams the contract's online SSE events so the
# browser can observe the live pipeline. Credentials are provisioned at startup
# as argon2id hashes for the E2E accounts. Migrations are checksummed and
# idempotent; setup still writes accounts and must use a disposable database.
set -euo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo "内部Playwright辅助：E2E_ISOLATED=1；必须显式指定三个同库DSN、独立REDIS_URL和FILE_STORE_ROOT。会迁移、注册alice/bob并启动Fake API；不得用于业务环境。"; exit 0
fi
[[ $# == 0 ]] || { echo "未知参数" >&2; exit 2; }
[[ "${E2E_ISOLATED:-}" == 1 ]] || { echo "必须显式设置 E2E_ISOLATED=1 并核实隔离资源" >&2; exit 2; }
for name in APP_DATABASE_URL WORKER_DATABASE_URL MIGRATION_DATABASE_URL REDIS_URL FILE_STORE_ROOT; do
  [[ -n "${!name:-}" ]] || { echo "必须显式设置 $name" >&2; exit 2; }
done
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python"
# Local dev uses the repo venv; CI installs into the system interpreter.
if [ ! -x "${PY}" ]; then
  PY="python3"
fi

export HPAGENT_ENV=test
export WEB_API_PORT="${WEB_API_PORT:-8180}"
export APP_DATABASE_URL
export WORKER_DATABASE_URL
export MIGRATION_DATABASE_URL
export REDIS_URL

# The browser origin the API accepts for CSRF (vite dev server). Cookies are
# non-secure because E2E runs over plain http://localhost.
export WEB_PUBLIC_ORIGIN="${WEB_PUBLIC_ORIGIN:-http://localhost:${WEB_DEV_PORT:-5273}}"
export WEB_COOKIE_SECURE=false
export WEB_CURSOR_SECRET="e2e-cursor-secret-0123456789abcdef-32bytes"
export WEB_SESSION_TOKEN_PEPPER="e2e-session-pepper-0123456789abcdef-32bytes"
export WEB_CSRF_SIGNING_KEY="e2e-csrf-signing-key-0123456789abcdef-32bytes"
export QQ_BINDING_CODE_PEPPER="e2e-qq-binding-code-pepper-0123456789abcdef"

# Fake executor: slow enough to click Stop and for a second tab to race the
# conversation-busy guard, fast enough to not drag the suite.
export WEB_FAKE_EXECUTOR_ENABLED=true
export WEB_FILE_UPLOAD_ENABLED=true
export FILE_STORE_ROOT
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

# Provision the predefined E2E accounts through the canonical registration
# service. Registration persists both the identity binding and the password
# credential required by the production authentication adapter.
E2E_PASSWORD="${E2E_PASSWORD:-e2e-password}"

# Ensure the schema is present (no-op when already applied).
(
  cd "${REPO_ROOT}"
  PYTHONPATH=src APP_DATABASE_URL="${MIGRATION_DATABASE_URL}" "$PY" -m persistence.migrate
)

# Migrations create the schema only. Keep setup idempotent for local reruns,
# while CI always exercises this against an empty database.
(
cd "${REPO_ROOT}"
E2E_PASSWORD="${E2E_PASSWORD}" PYTHONPATH=src "$PY" - <<'PY'
import os

from account.registration_service import RegistrationService, UsernameAlreadyExists

registration = RegistrationService(os.environ["MIGRATION_DATABASE_URL"])
for subject in ("alice", "bob"):
    try:
        registration.register(subject, os.environ["E2E_PASSWORD"])
    except UsernameAlreadyExists:
        pass
print("E2E accounts ensured: alice, bob")
PY
)

cd "${REPO_ROOT}"
exec env PYTHONPATH=src "$PY" -m web_api
