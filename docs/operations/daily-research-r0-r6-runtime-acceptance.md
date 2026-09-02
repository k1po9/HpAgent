# Daily Research R0–R6 Runtime 收口操作手册

本文用于完成 Daily Research 的运行时验收。执行完成且所有必选项通过后，才可以把当前代码从“R0–R6 代码 checkpoint”提升为“Research R0–R6 Complete”。

本文只覆盖 Research 收口，不进入 File Assistant、Docling、MarkItDown、OCR 或新的 Agent Framework。

## 1. 验收目标

最终需要证明四件事：

1. 新 migration 能在干净 PostgreSQL 中可靠应用。
2. 新 Docker 镜像包含 Research dependencies 和 Playwright Chromium。
3. 五类 Source Provider 能访问真实来源，并生成当前 Domain contract。
4. API、Outbox、Temporal、Evidence、Report、Artifact、Schedule 能形成完整、可恢复的闭环。

建议在 Linux/WSL 仓库根目录执行全部命令：

```bash
cd /home/hp/workspace/HpAgent_web
git branch --show-current
git status --short
```

原理：先确认分支仍为 `feat/hpagent-web`，并记录脏工作树。本文不要求 commit，也不应回退已有改动。

## 2. 前置工具与环境

检查本机工具：

```bash
docker version
docker compose version
curl --version
jq --version
git --version
.venv/bin/python --version
```

原理：Docker/Compose 负责真实部署，curl/jq 负责 API 验收，项目虚拟环境负责测试和 migration runner。

`jq` 只用于提高人工阅读体验；未安装时可使用
`python -m json.tool` 或短 Python 脚本解析响应，不应因此阻断验收。

### 2.1 WSL、Docker 与容器代理

不要把代理地址写死在仓库。先读取当前用户 `.bashrc` 中项目现场已有的
`proxy_all_on`，再在当前 shell 启用：

```bash
source ~/.bashrc
type proxy_all_on
proxy_all_on
```

为 Compose 内部通信补齐 bypass：

```bash
export NO_PROXY='localhost,127.0.0.1,::1,searxng,app-postgres,redis,temporal,hindsight,hpagent,hpagent-api'
export no_proxy="$NO_PROXY"
```

验证 WSL 与 Docker daemon 两条不同路径：

```bash
curl -fsS --max-time 30 https://api.ipify.org
systemctl show docker --property=Environment
docker pull alpine:3.20
```

原理：shell 代理、Docker daemon/build 代理、Compose container runtime 代理是三层独立配置。
`docker-compose.yaml` 会把当前 `HTTP_PROXY`/`HTTPS_PROXY` 作为构建参数和必要服务的运行时环境传入；
SearXNG 启动脚本再把当前代理渲染为正式 `outgoing.proxies` 配置。

准备本地环境文件：

```bash
test -f .env || cp .env.example .env
```

打开 `.env`，至少确认以下配置不是空值或错误地址：

```dotenv
HPAGENT_ENV=development
WEB_PUBLIC_ORIGIN=http://127.0.0.1:5173
WEB_COOKIE_SECURE=false
WEB_CREDENTIALS_JSON={}

HPAGENT_MIGRATE_PASSWORD=change-me
HPAGENT_API_PASSWORD=change-me
HPAGENT_WORKER_PASSWORD=change-me

SEARXNG_URL=http://searxng:8080
SEARXNG_SECRET=change-me

# 至少配置一套可用模型；变量名必须与 config/models.yaml 一致。
MINIMAX_API_KEY=
MINIMAX_API_BASE_URL=
MINIMAX_FLAGSHIP_MODEL=
SILICONFLOW_API_KEY=
SILICONFLOW_BASE_URL=
ALIBABA_BAILIAN_API_KEY=
ALIBABA_BAILIAN_BASE_URL=
ALIBABA_BAILIAN_FAST_MODEL=

# Runtime acceptance 使用现有国内 DeepSeek 配置做独立连通性验证。
DEEPSEEK_FLAGSHIP_MODEL=
DEEPSEEK_HIGHSPEED_MODEL=
DEEPSEEK_STANDARD_MODEL=
DEEPSEEK_API_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=
```

原理：Discovery/Fetch 可以不依赖模型，但最终 Report synthesis 需要真实可用的模型配置。生产环境不得继续使用示例密码和开发 secret。

验证 Compose 展开结果：

```bash
docker compose --profile web config >/tmp/hpagent-research-compose.yaml
docker compose --profile web config --services
```

预期至少包含：

```text
app-postgres
redis
searxng
temporal-postgres
temporal
hindsight-postgres
hindsight
hpagent-migrate
hpagent
hpagent-api
web-dev
```

原理：`config` 会在启动前发现 YAML、profile、变量插值和依赖关系错误。

## 3. 处理 migration checksum 漂移

### 3.1 先诊断，不要修改 migration history

查看数据库已经记录的 checksum：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d hpagent -P pager=off \
  -c "SELECT version, checksum, applied_at FROM hpagent.schema_migrations WHERE version LIKE '02%' ORDER BY version;"
```

计算工作树 SQL 的 checksum：

```bash
sha256sum persistence/migrations/021_research_r0_r2.sql
sha256sum persistence/migrations/022_research_r3_r4.sql
sha256sum persistence/migrations/023_research_r5_r6.sql
```

原理：migration runner 对文件内容做 SHA256。数据库记录与当前文件不一致意味着“已应用的历史被改写”，必须停下处理，不能直接更新 `schema_migrations.checksum` 来绕过保护。

先备份当前数据库：

```bash
mkdir -p .data/backups
docker compose exec -T app-postgres \
  pg_dump -U hpagent_migrate -d hpagent -Fc \
  > .data/backups/hpagent-before-research-r0-r6.dump
test -s .data/backups/hpagent-before-research-r0-r6.dump
```

原理：`-Fc` 生成可由 `pg_restore` 选择性恢复的自定义格式。`test -s` 确认备份不是空文件。

### 3.2 路径 A：开发数据允许丢弃时重建 volume

仅适用于可丢弃的本地开发库。先确认 volume 的精确对象：

```bash
docker volume inspect hpagent_web_app-pgdata
```

停止使用该数据库的 Compose 服务：

```bash
docker compose --profile web down
```

再次确认备份存在：

```bash
test -s .data/backups/hpagent-before-research-r0-r6.dump
```

删除且只删除已检查的开发数据库 volume：

```bash
docker volume rm hpagent_web_app-pgdata
```

重新启动 PostgreSQL 和 migration：

```bash
docker compose up -d --wait app-postgres
docker compose --profile web run --rm hpagent-migrate
```

原理：新 volume 会从 migration 001 开始执行，消除旧开发历史与当前 SQL 的冲突。删除 volume 不可原地恢复，需要时用上面的 dump 恢复。

### 3.3 路径 B：必须保留数据时使用前向 migration

不要删除 volume，也不要改数据库 checksum。先从产生当前数据库的 commit、构建产物或部署归档取回当时的 021/022：

```bash
git log --all -- persistence/migrations/021_research_r0_r2.sql
git log --all -- persistence/migrations/022_research_r3_r4.sql
```

将历史文件恢复为已应用版本后，确认其 SHA256 与数据库记录一致：

```bash
sha256sum persistence/migrations/021_research_r0_r2.sql
sha256sum persistence/migrations/022_research_r3_r4.sql
```

把后来对 021/022 的结构变化改写为新的、只向前执行的 migration，例如 `024_research_history_reconciliation.sql`，然后先在数据库副本验证：

```bash
createdb --version
```

如果原始 SQL 已无法找回，不要猜测或伪造 checksum；应保留备份并由数据负责人决定重建开发库还是从部署制品恢复历史文件。

原理：已经应用的 migration 是不可变历史。保留数据时，正确做法是恢复历史文件并添加新 migration，而不是让数据库相信一份从未执行过的 SQL 已经执行。

## 4. 在隔离数据库验证全部 migration

下面的测试会截断业务表，禁止指向生产数据库。创建名称固定、用途明确的验收库：

```bash
docker compose up -d --wait app-postgres
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d postgres \
  -c "CREATE DATABASE hpagent_research_acceptance OWNER hpagent_migrate;"
```

如果提示数据库已存在，先确认它确实是之前的验收库；不要对未知数据库执行后续测试。

设置三类最小权限连接。若 `.env` 使用了自定义密码，替换下面三个密码：

```bash
export RESEARCH_MIGRATION_DATABASE_URL='postgresql://hpagent_migrate:change-me@127.0.0.1:5434/hpagent_research_acceptance'
export RESEARCH_API_DATABASE_URL='postgresql://hpagent_api:change-me@127.0.0.1:5434/hpagent_research_acceptance'
export RESEARCH_WORKER_DATABASE_URL='postgresql://hpagent_worker:change-me@127.0.0.1:5434/hpagent_research_acceptance'
```

执行 migration：

```bash
PYTHONPATH=src MIGRATION_DATABASE_URL="$RESEARCH_MIGRATION_DATABASE_URL" \
  .venv/bin/python -c 'from persistence.migrate import migrate; import os; migrate(os.environ["MIGRATION_DATABASE_URL"])'
```

核对 021–023：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d hpagent_research_acceptance -P pager=off \
  -c "SELECT version, checksum IS NOT NULL AS checksum_recorded FROM hpagent.schema_migrations WHERE version LIKE '02%' ORDER BY version;"
```

原理：隔离库同时验证 migration 排序、SQL 完整性、role grant 和 checksum 记录，不受已有开发数据影响。

## 5. 构建新的 Runtime 镜像

确认 Docker Hub、GHCR、PyPI 能访问：

```bash
curl -I --max-time 20 https://registry-1.docker.io/v2/
curl -I --max-time 20 https://ghcr.io/v2/
curl -I --max-time 20 https://pypi.org/simple/httpx/
```

Docker Registry 返回 `401 Unauthorized` 是正常的匿名认证握手；DNS、TLS 或 timeout 才表示网络仍被阻断。

构建三个相关镜像：

```bash
docker compose --profile web build --pull hpagent-migrate hpagent hpagent-api
```

原理：`--pull` 验证基础镜像确实可获取；hpagent 镜像构建过程会安装 Research Python dependencies 和 Playwright Chromium。

验证镜像内依赖：

```bash
docker compose --profile web run --rm --no-deps hpagent \
  python -c "import httpx,trafilatura,feedparser,w3lib,githubkit,playwright; print('research imports: PASS')"
```

验证 Chromium 真正启动：

```bash
docker compose --profile web run --rm --no-deps hpagent python - <<'PY'
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True)
        print("chromium:", browser.version)
        await browser.close()

asyncio.run(main())
PY
```

原理：仅能 import Playwright 不代表浏览器二进制和系统动态库完整；实际 launch/close 才能确认 runtime。

## 6. 启动完整 Compose

```bash
docker compose --profile web up -d --wait
docker compose --profile web ps
```

检查 migration 容器成功退出：

```bash
docker compose ps -a hpagent-migrate
docker compose logs --no-color hpagent-migrate
```

检查 Worker/API/SearXNG：

```bash
docker compose logs --no-color --tail=200 hpagent
docker compose logs --no-color --tail=100 hpagent-api
docker compose logs --no-color --tail=100 searxng
curl -fsS http://127.0.0.1:8080/health/ready
curl -fsS http://127.0.0.1:8085/
```

验证 Worker 到 SearXNG 的 Docker DNS 和 JSON endpoint：

```bash
docker compose exec -T hpagent getent hosts searxng
docker compose exec -T hpagent python - <<'PY'
import json
import urllib.parse
import urllib.request

query = urllib.parse.urlencode({"q": "Python asyncio", "format": "json"})
with urllib.request.urlopen("http://searxng:8080/search?" + query, timeout=30) as response:
    payload = json.load(response)
print("results:", len(payload.get("results", [])))
assert isinstance(payload.get("results"), list)
assert payload["results"], "SearXNG returned no usable upstream results"
PY
```

原理：主机能访问端口不等于 Worker 容器能通过 service DNS 访问它；该命令验证真正的应用网络路径。

## 7. Source Smoke：SearXNG 与 URL canonicalization

```bash
docker compose exec -T hpagent python - <<'PY'
import asyncio
from research_adapters.web import SearXNGDiscoveryProvider, W3libSourceCanonicalizer
from research_domain.models import SourceStrategy

async def main():
    provider = SearXNGDiscoveryProvider("http://searxng:8080")
    candidates = await provider.discover(
        "Python 3.14 release notes",
        strategy=SourceStrategy(public_web=True, freshness_days=365),
        limit=5,
    )
    assert candidates
    canonicalizer = W3libSourceCanonicalizer()
    for item in candidates:
        print(item.provider, canonicalizer.canonicalize(item.uri), item.title)
        assert item.provider == "searxng"

asyncio.run(main())
PY
```

原理：这里调用生产 Adapter，而不是只请求裸 JSON，因此同时验证 JSON→`SourceCandidate` 和 w3lib canonical URI。

## 8. Source Smoke：httpx + Trafilatura

```bash
docker compose exec -T hpagent python - <<'PY'
import asyncio
from research_adapters.web import StaticWebContentProvider, W3libSourceCanonicalizer
from research_domain.models import SourceCandidate

async def main():
    provider = StaticWebContentProvider(W3libSourceCanonicalizer(), browser=None)
    content = await provider.fetch(SourceCandidate(
        uri="https://docs.python.org/3/library/asyncio.html",
        title="asyncio documentation",
        provider="acceptance",
    ))
    print(content.canonical_uri)
    print(content.metadata)
    print("characters:", len(content.text), "sha256:", content.content_hash)
    assert content.metadata["fetch_mode"] == "httpx"
    assert len(content.text) >= 240
    assert len(content.content_hash) == 64

asyncio.run(main())
PY
```

原理：`browser=None` 保证这个用例不可能偷偷调用 Playwright，从而证明静态网页走 httpx + Trafilatura 快路径。

## 9. Source Smoke：Playwright fallback

先创建一个确定性的 JS 页面。静态 HTML 正文很短，JavaScript 执行后才生成足够长的正文：

```bash
mkdir -p /tmp/hpagent-research-js-smoke
printf '%s\n' '<!doctype html><html><body><div id="app">short</div><script>document.getElementById("app").innerHTML="<h1>Rendered Research Source</h1><p>" + "browser fallback content ".repeat(80) + "</p>";</script></body></html>' \
  > /tmp/hpagent-research-js-smoke/index.html
```

解析 Compose 网络名称：

```bash
docker inspect "$(docker compose ps -q hpagent)" \
  --format '{{range $name, $config := .NetworkSettings.Networks}}{{$name}}{{end}}'
```

将输出的精确网络名替换到下一条命令的 `<COMPOSE_NETWORK>`：

```bash
docker run --rm -d --name hpagent-research-js-smoke \
  --network <COMPOSE_NETWORK> \
  -v /tmp/hpagent-research-js-smoke:/usr/share/nginx/html:ro \
  nginx:alpine
```

执行生产 fallback Adapter：

```bash
docker compose exec -T hpagent python - <<'PY'
import asyncio
from research_adapters.web import (
    PlaywrightBrowserFetchProvider,
    StaticWebContentProvider,
    W3libSourceCanonicalizer,
)
from research_domain.models import SourceCandidate

async def main():
    provider = StaticWebContentProvider(
        W3libSourceCanonicalizer(),
        browser=PlaywrightBrowserFetchProvider(),
        min_content_chars=240,
    )
    content = await provider.fetch(SourceCandidate(
        uri="http://hpagent-research-js-smoke/",
        title="JS fallback acceptance",
        provider="acceptance",
    ))
    print(content.metadata, "characters:", len(content.text))
    assert content.metadata["fetch_mode"] == "playwright"
    assert "Rendered Research Source" in content.text

asyncio.run(main())
PY
```

清理测试容器和临时页面：

```bash
docker stop hpagent-research-js-smoke
rm -r /tmp/hpagent-research-js-smoke
```

原理：这是可重复的 fallback 控制流测试，不依赖某个第三方网站恰好采用特定 JS 框架。Adapter 每次 fallback 都通过 context manager 启动并关闭 Chromium。

## 10. Source Smoke：GitHubKit

可选配置 token 以避免匿名 API rate limit：

```bash
export GITHUB_TOKEN='<OPTIONAL_PUBLIC_REPO_TOKEN>'
```

如果使用 token，需要确保它传入 hpagent 容器。未配置时下面命令使用匿名 public API：

```bash
docker compose exec -T hpagent python - <<'PY'
import asyncio
from research_adapters.discovery import GitHubDiscoveryProvider
from research_domain.models import SourceStrategy

async def main():
    candidates = await GitHubDiscoveryProvider().discover(
        "temporalio sdk-python",
        strategy=SourceStrategy(public_web=False, github=True, rss=False),
        limit=3,
    )
    assert candidates
    for item in candidates:
        print(item.provider, item.source_type, item.uri, item.title)
        assert item.provider == "githubkit"
        assert item.source_type == "github_repository"

asyncio.run(main())
PY
```

若必须使用 token，改为：

```bash
docker compose exec -T -e GITHUB_TOKEN="$GITHUB_TOKEN" hpagent python - <<'PY'
import asyncio
from research_adapters.discovery import GitHubDiscoveryProvider
from research_domain.models import SourceStrategy

async def main():
    values = await GitHubDiscoveryProvider().discover(
        "temporalio sdk-python",
        strategy=SourceStrategy(github=True),
        limit=3,
    )
    assert values
    print([(value.uri, value.title) for value in values])

asyncio.run(main())
PY
```

原理：直接调用 githubkit 生成的异步 REST client，并验证结果已转换成 HpAgent `SourceCandidate`。

## 11. Source Smoke：RSS / Atom

```bash
docker compose exec -T hpagent python - <<'PY'
import asyncio
from research_adapters.discovery import RSSDiscoveryProvider
from research_domain.models import SourceStrategy

async def main():
    strategy = SourceStrategy(
        public_web=False,
        github=False,
        rss=True,
        rss_feeds=("https://planetpython.org/rss20.xml",),
    )
    candidates = await RSSDiscoveryProvider().discover(
        "python", strategy=strategy, limit=5
    )
    assert candidates
    for item in candidates:
        print(item.provider, item.source_type, item.uri, item.title)
        assert item.provider == "feedparser"
        assert item.source_type == "rss_entry"

asyncio.run(main())
PY
```

原理：该链路实际执行 `httpx → feedparser → SourceCandidate`。若 feed 可访问但关键词没有匹配，可以把查询改为该 feed 最近条目的关键词，不能把空结果标为 PASS。

## 12. 登录并创建真实 Research Task

先确保数据库已有可登录的 Web 身份。已有用户可以直接使用；新环境参考 `docs/operations/web-registration-and-qq-binding.md` 创建身份。

设置本次 smoke 变量：

```bash
export RESEARCH_GATEWAY_URL='http://127.0.0.1:5173'
export RESEARCH_SMOKE_USER='admin'
export RESEARCH_SMOKE_PASSWORD='smoke-password'
export RESEARCH_COOKIE_JAR="$(mktemp)"
```

登录：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -c "$RESEARCH_COOKIE_JAR" -b "$RESEARCH_COOKIE_JAR" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$RESEARCH_SMOKE_USER\",\"password\":\"$RESEARCH_SMOKE_PASSWORD\",\"return_to\":\"/\"}" \
  "$RESEARCH_GATEWAY_URL/auth/login"
```

预期状态码为 `303`。读取 session 和 CSRF：

```bash
export RESEARCH_ME_JSON="$(curl -sS -b "$RESEARCH_COOKIE_JAR" "$RESEARCH_GATEWAY_URL/api/v1/me")"
export RESEARCH_CSRF_TOKEN="$(printf '%s' "$RESEARCH_ME_JSON" | jq -r '.csrf_token')"
export RESEARCH_ACCOUNT_ID="$(printf '%s' "$RESEARCH_ME_JSON" | jq -r '.account.account_id')"
printf 'account=%s\n' "$RESEARCH_ACCOUNT_ID"
```

创建 Task：

```bash
export RESEARCH_CREATE_KEY="$(cat /proc/sys/kernel/random/uuid)"
export RESEARCH_TASK_JSON="$(curl -sS -b "$RESEARCH_COOKIE_JAR" \
  -H "Origin: $RESEARCH_GATEWAY_URL" \
  -H "X-CSRF-Token: $RESEARCH_CSRF_TOKEN" \
  -H "Idempotency-Key: $RESEARCH_CREATE_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Daily Research runtime acceptance","objective":"Research the latest stable Python and Temporal Python SDK changes using public web, GitHub and RSS; produce cited findings.","source_strategy":{"public_web":true,"official_sources":true,"github":true,"rss":true,"uploaded_files":false,"freshness_days":365,"preferred_domains":["python.org","github.com","temporal.io"],"rss_feeds":["https://planetpython.org/rss20.xml"]}}' \
  "$RESEARCH_GATEWAY_URL/api/v1/tasks")"
export RESEARCH_TASK_ID="$(printf '%s' "$RESEARCH_TASK_JSON" | jq -r '.task.task_id')"
printf '%s\n' "$RESEARCH_TASK_JSON" | jq
```

确认 `RESEARCH_TASK_ID` 不是 `null`：

```bash
test -n "$RESEARCH_TASK_ID" && test "$RESEARCH_TASK_ID" != null
```

原理：Task 创建通过 API、CSRF、account ownership 和 Command idempotency，不直接向数据库插入业务记录。

## 13. 触发 Run 并等待终态

```bash
export RESEARCH_TRIGGER_KEY="$(cat /proc/sys/kernel/random/uuid)"
export RESEARCH_RUN_JSON="$(curl -sS -b "$RESEARCH_COOKIE_JAR" \
  -H "Origin: $RESEARCH_GATEWAY_URL" \
  -H "X-CSRF-Token: $RESEARCH_CSRF_TOKEN" \
  -H "Idempotency-Key: $RESEARCH_TRIGGER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{}' \
  "$RESEARCH_GATEWAY_URL/api/v1/tasks/$RESEARCH_TASK_ID/runs")"
export RESEARCH_RUN_ID="$(printf '%s' "$RESEARCH_RUN_JSON" | jq -r '.run.run_id')"
printf '%s\n' "$RESEARCH_RUN_JSON" | jq
```

轮询，直到 completed 或明确失败：

```bash
for RESEARCH_POLL_INDEX in $(seq 1 180); do
  RESEARCH_STATUS_JSON="$(curl -sS -b "$RESEARCH_COOKIE_JAR" \
    "$RESEARCH_GATEWAY_URL/api/v1/tasks/$RESEARCH_TASK_ID/runs/$RESEARCH_RUN_ID")"
  RESEARCH_RUN_STATUS="$(printf '%s' "$RESEARCH_STATUS_JSON" | jq -r '.run.status')"
  printf 'poll=%s status=%s\n' "$RESEARCH_POLL_INDEX" "$RESEARCH_RUN_STATUS"
  case "$RESEARCH_RUN_STATUS" in
    completed) break ;;
    failed|cancelled) printf '%s\n' "$RESEARCH_STATUS_JSON" | jq; exit 1 ;;
  esac
  sleep 2
done
test "$RESEARCH_RUN_STATUS" = completed
```

原理：Run 必须由 Outbox dispatcher 异步启动 Temporal Workflow。轮询 API 能证明 API 读取的业务状态最终与 Worker 执行结果一致。

查询 Evidence 和 Report：

```bash
curl -sS -b "$RESEARCH_COOKIE_JAR" \
  "$RESEARCH_GATEWAY_URL/api/v1/tasks/$RESEARCH_TASK_ID/runs/$RESEARCH_RUN_ID/evidence" | jq
curl -sS -b "$RESEARCH_COOKIE_JAR" \
  "$RESEARCH_GATEWAY_URL/api/v1/tasks/$RESEARCH_TASK_ID/runs/$RESEARCH_RUN_ID/report" | jq
```

## 14. PostgreSQL 关联链验收

检查 Task、Run、Budget、Outbox、Workflow：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d hpagent -P pager=off \
  -v task_id="$RESEARCH_TASK_ID" -v run_id="$RESEARCH_RUN_ID" <<'SQL'
SET search_path TO hpagent, public;
SELECT task_id, account_id, status, schedule_type FROM tasks WHERE task_id=:'task_id';
SELECT run_id, task_id, run_kind, status, workflow_id FROM runs WHERE run_id=:'run_id';
SELECT run_id, mode, status, limits, used FROM run_budgets WHERE run_id=:'run_id';
SELECT event_type, status, attempt_count, processed_at FROM outbox_events WHERE run_id=:'run_id';
SELECT workflow_id, temporal_run_id, status, is_current FROM workflow_executions WHERE run_id=:'run_id';
SQL
```

检查 Research 数据和 Artifact：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d hpagent -P pager=off \
  -v run_id="$RESEARCH_RUN_ID" <<'SQL'
SET search_path TO hpagent, public;
SELECT run_id, plan_version, plan FROM research_plans WHERE run_id=:'run_id';
SELECT iteration, status, query FROM research_iterations WHERE run_id=:'run_id' ORDER BY iteration;
SELECT source_id, provider, source_type, canonical_uri, fetch_status, content_hash, duplicate_of_source_id FROM source_records WHERE run_id=:'run_id' ORDER BY created_at;
SELECT sc.source_id, length(sc.content_text) AS chars, sc.byte_size FROM source_contents sc JOIN source_records sr USING(source_id) WHERE sr.run_id=:'run_id';
SELECT evidence_id, source_id, source_quality, source_locator FROM evidence_items WHERE run_id=:'run_id';
SELECT claim_id, ordinal, importance, evidence_status, statement FROM research_claims WHERE run_id=:'run_id' ORDER BY ordinal;
SELECT citation_id, claim_id, evidence_id, verification_status, locator FROM research_citations WHERE run_id=:'run_id';
SELECT run_id, previous_run_id, citation_status, artifact_id, artifact_version_id, snapshot, daily_diff FROM research_reports WHERE run_id=:'run_id';
SELECT a.artifact_id, a.research_run_id, a.account_id, a.title, av.artifact_version_id, av.version, av.status, length(av.html) AS html_chars
  FROM artifacts a JOIN artifact_versions av USING(artifact_id)
  WHERE a.research_run_id=:'run_id';
SQL
```

最少断言：

- 每张表都能由同一个真实 Run ID 串联。
- `outbox_events.event_type='start_research_run'` 且最终 processed。
- iteration 数量为 1–3。
- SourceRecord 有 canonical URI 和 content hash。
- Evidence locator 能回到 SourceContent。
- Citation 的 Evidence 属于当前 Run。
- Report 同时有 Markdown、structured JSON、Artifact ID 和 Version ID。
- 一个 Research Run 只有一个 Artifact 和一个首发 Version。

## 15. 第二次 Run 与 Daily Diff

再次使用新的 idempotency key 触发同一 Task：

```bash
export RESEARCH_SECOND_TRIGGER_KEY="$(cat /proc/sys/kernel/random/uuid)"
export RESEARCH_SECOND_RUN_JSON="$(curl -sS -b "$RESEARCH_COOKIE_JAR" \
  -H "Origin: $RESEARCH_GATEWAY_URL" \
  -H "X-CSRF-Token: $RESEARCH_CSRF_TOKEN" \
  -H "Idempotency-Key: $RESEARCH_SECOND_TRIGGER_KEY" \
  -H 'Content-Type: application/json' -d '{}' \
  "$RESEARCH_GATEWAY_URL/api/v1/tasks/$RESEARCH_TASK_ID/runs")"
export RESEARCH_SECOND_RUN_ID="$(printf '%s' "$RESEARCH_SECOND_RUN_JSON" | jq -r '.run.run_id')"
printf '%s\n' "$RESEARCH_SECOND_RUN_JSON" | jq
```

按第 13 节方式等待第二次 Run completed，然后检查 Diff：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d hpagent -P pager=off \
  -v run_id="$RESEARCH_SECOND_RUN_ID" \
  -c "SET search_path TO hpagent,public; SELECT previous_run_id, daily_diff FROM research_reports WHERE run_id=:'run_id';"
```

原理：第二次成功运行必须读取同一 Task 的前一成功报告，并形成 new/changed/continuing/invalidated，而不是做 Markdown diff。

## 16. Schedule 验收

生产 API 只接受 manual/daily。先创建 daily 配置：

```bash
export RESEARCH_SCHEDULE_KEY="$(cat /proc/sys/kernel/random/uuid)"
curl -sS -b "$RESEARCH_COOKIE_JAR" \
  -H "Origin: $RESEARCH_GATEWAY_URL" \
  -H "X-CSRF-Token: $RESEARCH_CSRF_TOKEN" \
  -H "Idempotency-Key: $RESEARCH_SCHEDULE_KEY" \
  -H 'Content-Type: application/json' \
  -X PUT \
  -d '{"schedule_type":"daily","timezone":"Asia/Shanghai","expression":"09:00","enabled":true}' \
  "$RESEARCH_GATEWAY_URL/api/v1/tasks/$RESEARCH_TASK_ID/schedule" | jq
```

等待 reconciliation，并列出 Temporal Schedule：

```bash
sleep 5
docker compose exec -T temporal temporal schedule list
```

生产配置不能为了测试暴露秒级 cron。短周期真实触发由现有 Temporal integration test 完成：

```bash
docker compose exec -T temporal temporal operator namespace describe \
  --namespace hpagent-research-test \
  || docker compose exec -T temporal temporal operator namespace create \
     --namespace hpagent-research-test --retention 24h
```

原理：测试使用独立 namespace 和短周期测试配置，不改变生产 API 的 manual/daily contract。

```bash
TEMPORAL_HOST=127.0.0.1:7233 \
MIGRATION_DATABASE_URL="$RESEARCH_MIGRATION_DATABASE_URL" \
APP_DATABASE_URL="$RESEARCH_API_DATABASE_URL" \
WORKER_DATABASE_URL="$RESEARCH_WORKER_DATABASE_URL" \
PYTHONPATH=. \
  .venv/bin/pytest -q test/web_persistence/test_research_schedule_temporal_integration.py
```

更新时间必须使用新的 key：

```bash
export RESEARCH_SCHEDULE_UPDATE_KEY="$(cat /proc/sys/kernel/random/uuid)"
curl -sS -b "$RESEARCH_COOKIE_JAR" \
  -H "Origin: $RESEARCH_GATEWAY_URL" \
  -H "X-CSRF-Token: $RESEARCH_CSRF_TOKEN" \
  -H "Idempotency-Key: $RESEARCH_SCHEDULE_UPDATE_KEY" \
  -H 'Content-Type: application/json' -X PUT \
  -d '{"schedule_type":"daily","timezone":"Asia/Shanghai","expression":"10:30","enabled":true}' \
  "$RESEARCH_GATEWAY_URL/api/v1/tasks/$RESEARCH_TASK_ID/schedule" | jq
```

禁用：

```bash
export RESEARCH_SCHEDULE_DISABLE_KEY="$(cat /proc/sys/kernel/random/uuid)"
curl -sS -b "$RESEARCH_COOKIE_JAR" \
  -H "Origin: $RESEARCH_GATEWAY_URL" \
  -H "X-CSRF-Token: $RESEARCH_CSRF_TOKEN" \
  -H "Idempotency-Key: $RESEARCH_SCHEDULE_DISABLE_KEY" \
  -H 'Content-Type: application/json' -X PUT \
  -d '{"schedule_type":"manual","timezone":"Asia/Shanghai","expression":null,"enabled":false}' \
  "$RESEARCH_GATEWAY_URL/api/v1/tasks/$RESEARCH_TASK_ID/schedule" | jq
```

原理：Schedule 只负责触发 Task Command；Run、Budget 和 Outbox 仍由同一 Domain transaction 创建。

## 17. Temporal、Replay 与 Worker Kill

第 16 节已经注册 `hpagent-research-test` namespace；以下测试继续使用该隔离 namespace，避免与 `default` 中的开发 Workflow 和 Schedule 相互干扰。

真实 Temporal 控制流和 History replay：

```bash
TEMPORAL_HOST=127.0.0.1:7233 PYTHONPATH=. \
  .venv/bin/pytest -q test/test_research_temporal_integration.py
```

真实 Fetch Activity Worker kill/restart：

```bash
TEMPORAL_HOST=127.0.0.1:7233 PYTHONPATH=. TMPDIR=/tmp/hpagent-pytest \
  .venv/bin/pytest -q test/test_research_worker_kill.py
```

契约测试：

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  test/test_research_temporal_contract.py \
  test/test_web_temporal_contract.py \
  test/test_durable_agent_contract.py
```

原理：integration test 验证真实 server execution 和 replay；worker-kill test 验证 Fetch 心跳超时后由替代 Worker 重试，且不会创建第二个业务 Run。

## 18. Evidence、Budget、Artifact 与隔离测试

```bash
MIGRATION_DATABASE_URL="$RESEARCH_MIGRATION_DATABASE_URL" \
APP_DATABASE_URL="$RESEARCH_API_DATABASE_URL" \
WORKER_DATABASE_URL="$RESEARCH_WORKER_DATABASE_URL" \
REDIS_URL=redis://127.0.0.1:6379 \
PYTHONPATH=. \
  .venv/bin/pytest -q \
  test/test_research_domain.py \
  test/test_research_schedule.py \
  test/web_persistence/test_research_r0_r2.py
```

这些测试覆盖：

- No Evidence、Weak Evidence、Tier 3 only。
- Unknown、跨 Run、跨 tenant Evidence。
- Invalid Locator 和 Citation verification failure。
- sources discovered、fetch、iteration、model、wall time budget exhaustion。
- Artifact publish retry 幂等。
- 两次 Run 的 Artifact 语义和 Daily Diff。
- Task/Run/Evidence/Report/Artifact ownership。

原理：这里显式传入 API、Worker、migration 三种数据库角色，能同时验证权限边界，而不是全部使用管理员连接。

## 19. 完整 regression 和静态检查

先跑整个测试目录。注意：测试 fixture 会截断验收数据库中的业务表：

生产 `hpagent` worker 和部分真实 Temporal integration tests 使用相同的默认 task queue。
为防止生产 worker 抢走测试 Activity，完整 suite 前先暂停它，测试结束后必须恢复：

```bash
docker compose stop hpagent
```

```bash
TEMPORAL_HOST=127.0.0.1:7233 \
MIGRATION_DATABASE_URL="$RESEARCH_MIGRATION_DATABASE_URL" \
APP_DATABASE_URL="$RESEARCH_API_DATABASE_URL" \
WORKER_DATABASE_URL="$RESEARCH_WORKER_DATABASE_URL" \
REDIS_URL=redis://127.0.0.1:6379 \
PYTHONPATH=. TMPDIR=/tmp/hpagent-pytest \
  .venv/bin/pytest -q test
```

```bash
docker compose start hpagent
```

任何失败都应记录首次错误，然后只复跑失败节点定位；最终仍需重新执行一次完整 suite，不能仅用分散复跑代替全绿结果。

Research 定向 lint/type/compile：

```bash
.venv/bin/ruff check \
  src/research_domain src/research_adapters src/research_activities \
  src/orchestration/research_workflow.py src/orchestration/research_schedule.py \
  test/test_research_domain.py test/test_research_schedule.py \
  test/test_research_temporal_contract.py test/test_research_temporal_integration.py \
  test/test_research_worker_kill.py test/support/research_worker_process.py \
  test/web_persistence/test_research_r0_r2.py \
  test/web_persistence/test_research_schedule_temporal_integration.py
```

```bash
.venv/bin/mypy --follow-imports=skip \
  src/research_domain src/research_adapters src/research_activities \
  src/orchestration/research_workflow.py src/orchestration/research_schedule.py
```

```bash
.venv/bin/python -m compileall -q \
  src/research_domain src/research_adapters src/research_activities \
  src/orchestration/research_workflow.py src/orchestration/research_schedule.py
```

仓库级检查：

```bash
.venv/bin/ruff check .
git diff --check
docker compose --profile web config >/dev/null
```

原理：定向检查用于阻止 Research 新增问题；仓库级检查用于判断是否具备形成整个分支 checkpoint 的条件。若仓库级 Ruff 仍有历史问题，应保存精确输出并确认 Research 文件没有新增错误。

## 20. 日志与失败定位

Migration 失败：

```bash
docker compose logs --no-color hpagent-migrate
```

Outbox/Worker/Temporal 失败：

```bash
docker compose logs --no-color --since=30m hpagent
docker compose logs --no-color --since=30m temporal
```

API ownership 或状态读取失败：

```bash
docker compose logs --no-color --since=30m hpagent-api
tail -n 200 .data/logs/web-api-error.log
```

SearXNG 上游失败：

```bash
docker compose logs --no-color --since=30m searxng
```

先从 PostgreSQL 读取指定 Run 的真实 Workflow ID：

```bash
export RESEARCH_WORKFLOW_ID="$(docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d hpagent -At \
  -v run_id="$RESEARCH_RUN_ID" \
  -c "SET search_path TO hpagent,public; SELECT workflow_id FROM runs WHERE run_id=:'run_id';")"
printf 'workflow_id=%s\n' "$RESEARCH_WORKFLOW_ID"
```

再查看 Temporal History：

```bash
docker compose exec -T temporal temporal workflow show \
  --workflow-id "$RESEARCH_WORKFLOW_ID"
```

原理：按 Migration→API/Outbox→Temporal/Activity→Source 的边界定位，避免在证据不足时重构整条链路。

## 21. 验收清理

删除 cookie 临时文件并取消 shell 变量：

```bash
rm -f "$RESEARCH_COOKIE_JAR"
unset RESEARCH_COOKIE_JAR RESEARCH_ME_JSON RESEARCH_CSRF_TOKEN
unset RESEARCH_CREATE_KEY RESEARCH_TRIGGER_KEY RESEARCH_SECOND_TRIGGER_KEY
unset RESEARCH_SCHEDULE_KEY RESEARCH_SCHEDULE_UPDATE_KEY RESEARCH_SCHEDULE_DISABLE_KEY
```

隔离验收数据库确认不再需要后，才执行：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d postgres \
  -c "DROP DATABASE hpagent_research_acceptance WITH (FORCE);"
```

原理：测试数据库删除不可恢复；只有测试结果和必要证据已经保存后才能执行。不要把数据库名替换为 `hpagent`。

## 22. 最终判定清单

只有以下项目全部 PASS，才能宣布 `Research R0–R6 Complete`：

```text
[x] 当前 migration history 有明确、可部署的处理结果
[x] 干净数据库执行 001–023 PASS
[x] docker compose build PASS
[x] hpagent / hpagent-api / PostgreSQL / Redis / Temporal / Hindsight / SearXNG healthy
[x] Research imports PASS
[x] Playwright Chromium launch/close PASS
[x] SearXNG Adapter real query PASS
[x] httpx + Trafilatura real static fetch PASS
[x] Playwright fallback PASS
[x] GitHubKit public source PASS
[x] RSS/Atom real source PASS
[x] API → Task → Run → Budget → Outbox → Temporal PASS
[x] Source → Evidence → Claim → Citation → Report → Artifact PASS
[x] Previous Snapshot / Daily Diff PASS
[x] Schedule fire/update/duplicate/disable PASS
[x] Temporal first-round sufficient PASS
[x] Temporal three-round upper bound PASS
[x] History replay PASS
[x] Activity Worker kill/recovery PASS
[x] Artifact retry idempotency PASS
[x] Budget exhaustion PASS
[x] Multi-tenant isolation PASS
[x] 完整 pytest suite 单次全绿
[x] Research Ruff/Mypy/compileall PASS
[x] git diff --check 与 Compose config PASS
```

任一必选项失败时，结论必须保持：

```text
B. 尚不能 Complete，并列出失败命令、错误摘要和真实阻塞项。
```

全部通过后才可以输出：

```text
A. Research R0–R6 Complete，可以形成稳定 checkpoint。
```

之后再规划 File Assistant F0+。

## 23. 2026-09-02 实际执行记录

### 23.1 搜索确认后的当前架构

生产链路已按真实类名确认并运行：

```text
POST /api/v1/tasks
  -> ResearchTaskCommandService.create_task
POST /api/v1/tasks/{task_id}/runs
  -> ResearchTaskCommandService.trigger_task
  -> PostgreSQL UnitOfWork
     -> runs + run_budgets + idempotency_commands
     -> transactional outbox: start_research_run
  -> WebOutboxDispatcher
  -> Temporal ResearchReportWorkflow
  -> ResearchActivities
     -> CompositeSourceDiscoveryProvider
        -> SearXNGDiscoveryProvider
        -> GitHubDiscoveryProvider (githubkit)
        -> RSSDiscoveryProvider (httpx + feedparser)
     -> StaticWebContentProvider (httpx + Trafilatura)
        -> PlaywrightBrowserFetchProvider fallback
     -> W3libSourceCanonicalizer
     -> ResearchRepository
        -> SourceRecord / SourceContent / EvidenceItem
        -> Claim / Citation / ResearchReport
     -> ResourcePoolResearchSynthesizer
     -> ArtifactService
  -> PostgresTraceRepository + RunBudgetService
```

Temporal History 只保存 `ResearchStageRef` compact reference；SourceContent、Evidence、Report
正文均保存在 PostgreSQL。原有 PostgreSQL Repository/UoW、transactional Outbox、Run/Run Budget、
ActionRuntime、Tool Registry、Trace、Artifact、RunFileScope 和 side-effect/idempotency 架构均保留。

### 23.2 Migration 与备份

- 备份：`.data/backups/hpagent-before-research-r0-r6-20260902.dump`
- 大小：163 KiB
- SHA256：`1db823a3365e0003b3221c3567a5510b0e04f4c1e564272d227884346cf33782`
- 已删除并重建精确开发 volume：`hpagent_web_app-pgdata`
- 从空库顺序执行 001–023：PASS，共 23 条记录
- 021 checksum 前缀：`8db62c43245a`
- 022 checksum 前缀：`55947a98d39f`
- 023 checksum 前缀：`7b5919e0285d`
- 未手工修改或伪造 `schema_migrations.checksum`

### 23.3 网络、容器与模型

- 从 `~/.bashrc` 的 `proxy_all_on` 读取当前 WSL gateway 和端口，WSL 代理出网：PASS
- Docker daemon/build pull：PASS（`alpine:3.20` 与构建基础镜像）
- APT、PyPI、Playwright CDN：PASS
- hpagent container runtime 出网：PASS
- `NO_PROXY` 覆盖全部要求的 Compose 服务名：PASS
- SearXNG container 出网：PASS
- SearXNG 正式 `outgoing.proxies` 动态渲染：PASS
- SearXNG upstream real query：PASS，实测 20–30 个结果
- GitHub API：HTTP 200
- Planet Python RSS：HTTP 200，25 entries
- httpx + Trafilatura：HTTP 200，提取 2021 字符，SHA256 64 位
- Playwright Chromium：`151.0.7922.34`，真实 launch/render/close PASS
- WSL DeepSeek `/models` 与最小 completion：HTTP 200
- hpagent container DeepSeek 最小 completion：HTTP 200，模型 `deepseek-v4-flash`

### 23.4 Provider 与 Runtime E2E

- SearXNG Adapter + w3lib canonicalization：5 candidates，PASS
- GitHubKit public repository discovery：PASS
- RSS/Atom `httpx -> feedparser -> SourceCandidate`：5 candidates，PASS
- 静态 fetch 快路径：`fetch_mode=httpx`，PASS
- 确定性 JS 页面 Playwright fallback：`fetch_mode=playwright`，4254 字符，PASS
- 两个真实 API Research Run：均 `completed`
- 每个 Run：30 discovered、20 fetch budget、18 fetched、18 Evidence、1 model call、1 iteration
- 两个 Outbox：`start_research_run`，均一次处理成功
- 两个 WorkflowExecution：均 `completed` 且 `is_current=true`
- 第一个报告：5 claims、9 citations、1 Artifact/1 Version
- 第二个报告：8 claims、15 citations、1 Artifact/1 Version
- 第二个报告正确引用第一个 `previous_run_id`，并生成 structured Daily Diff
- 两个 TraceRun：均 `completed`，各 13 个 completed TraceEvent

实测 ID（仅用于该开发库定位）：

```text
task_id:       01a05f9f-4fa0-71e2-bfb9-ce1eb8817828
first_run_id:  01a05f9f-4fc4-7f72-8155-c9c2b542ccdd
second_run_id: 01a05fa3-0b75-77b3-83a9-783e5c5a441c
```

### 23.5 Schedule、Temporal、Budget 与隔离

- daily schedule create：PASS
- 相同 idempotency key 重放：PASS，返回同一 schedule version
- daily `09:00 -> 10:30` update：PASS
- manual/disabled 与 Temporal Schedule 删除：PASS
- 独立 namespace 短周期真实 fire：`1 passed in 8.08s`
- Research Temporal execution/replay、三轮上限、worker kill/recovery 与 contract：
  `67 passed in 26.35s`
- Evidence、Budget exhaustion、Artifact retry、Daily Diff、multi-tenant isolation：
  `21 passed in 52.77s`
- Fetch Activity heartbeat retry 的 budget reservation 冲突已修复并增加 PostgreSQL 回归测试
- WSL 时钟小幅回拨导致 terminal timestamp 早于 created timestamp 的问题已修复并增加回归测试
- Hindsight/Research config 字段归属回归已修复；worker 日志确认
  `HindsightClient initialized: base_url=http://hindsight:8888`

### 23.6 Docker、依赖与配置改动

- `docker-compose.yaml`：SearXNG/hpagent/Hindsight runtime proxy、内部 `NO_PROXY`、build args、DeepSeek env
- `config/searxng/settings.yml`：正式 outgoing timeout 配置
- `config/searxng/entrypoint.sh`：从当前环境动态渲染 SearXNG `outgoing.proxies`
- `src/Dockerfile`：APT retry/`--fix-missing`；worker 安装 Playwright Chromium
- `src/Dockerfile.web-api`、`src/Dockerfile.migrate`：启用 BuildKit pip cache mount
- `requirements.txt`、`src/requirements.txt`：Research P0 dependencies
- 未为普通 Python parser 新建微服务；Playwright 仅 fallback 时启动；未引入 GPU 或商业 API 强依赖

Research 相关改动文件按职责归类如下：

```text
Domain / persistence
  src/research_domain/__init__.py
  src/research_domain/models.py
  src/research_domain/providers.py
  src/research_domain/persistence.py
  src/research_domain/services.py
  persistence/migrations/021_research_r0_r2.sql
  persistence/migrations/022_research_r3_r4.sql
  persistence/migrations/023_research_r5_r6.sql

Provider / Adapter / Activity
  src/research_adapters/__init__.py
  src/research_adapters/discovery.py
  src/research_adapters/web.py
  src/research_adapters/synthesis.py
  src/research_activities/__init__.py
  src/research_activities/runtime.py

Workflow / Outbox / Runtime wiring
  src/orchestration/research_workflow.py
  src/orchestration/research_schedule.py
  src/orchestration/web_dispatcher.py
  src/orchestration/web_workers.py
  src/orchestration/worker.py
  src/orchestration/config.py
  src/web_domain/outbox.py
  src/web_domain/workflow_execution.py
  src/agent_execution/run_budget.py
  src/agent_execution/tracing/repository.py

API / Repository / Artifact compatibility
  src/web_api/app.py
  src/web_api/models.py
  src/persistence/repositories.py
  src/web_artifacts/services.py

Runtime / dependencies
  docker-compose.yaml
  .env.example
  requirements.txt
  src/requirements.txt
  src/Dockerfile
  src/Dockerfile.migrate
  src/Dockerfile.web-api
  config/searxng/settings.yml
  config/searxng/entrypoint.sh

Tests / operations
  test/test_research_domain.py
  test/test_research_schedule.py
  test/test_research_temporal_contract.py
  test/test_research_temporal_integration.py
  test/test_research_worker_kill.py
  test/support/research_worker_process.py
  test/web_persistence/test_research_r0_r2.py
  test/web_persistence/test_research_schedule_temporal_integration.py
  test/web_persistence/test_phase_a_invariants.py
  docs/operations/daily-research-r0-r6-runtime-acceptance.md
```

### 23.7 Prototype 清理

- `tools/skills/daily_report.yaml`：已删除，不再约束正式 Research 架构
- `src/napcat-proxy/` 旧 prototype：已删除
- Production Runtime 与 Compatibility Required 路径保留
- 示例保留在 `tools/examples/`，不进入生产 Provider/Workflow contract

### 23.8 测试与静态检查最终结果

```text
full pytest:        642 passed, 1 warning in 1277.66s (21:17)
Research Ruff:      PASS
Research Mypy:      PASS, 13 source files
Research compileall: PASS
git diff --check:   PASS
Compose config:     PASS
```

唯一 warning 是 FastAPI/Starlette TestClient 对旧 `httpx` adapter 的弃用提示，不影响结果。
仓库级 `ruff check .` 仍报告 128 个既有非 Research 问题，主要位于旧 scripts、legacy
agent/channel 及旧测试；Research 定向范围为零错误。本轮未扩大范围批量格式化这些 legacy 文件。

### 23.9 验收中修复的运行时问题

1. Fetch Activity 在 heartbeat retry 后剩余行数变化，复用同一 budget operation 时发生维度冲突；
   现在从 ledger 恢复原 reservation，并对已 settled mutation 幂等处理。
2. MiniMax reasoning 模式可能耗尽输出 token 且只返回思考内容；Research synthesis 改用现有
   `chat` chain（禁用 thinking），并可靠提取被 prose/fence 包裹的完整 JSON object。
3. SearXNG 自身 HTTP client 不读取通用 proxy env；增加启动期正式 outgoing proxy 渲染。
4. Hindsight mission 字段因 `ResearchConfig` 插入位置错误而丢失；已恢复至 `HindsightConfig`。
5. WSL 恢复后的小时钟回拨可能违反 terminal timestamp constraint；start/terminal 时间改为
   对 `created_at`/`started_at` 单调不减。
6. 完整 Temporal 测试必须暂停生产 worker，避免双方消费相同默认 task queue；操作手册已补充。

### 23.10 最终判定

第 22 节 Research 必选项全部 PASS。仓库级 Ruff 的 128 个 legacy issue 不属于本次 Research
实现新增问题，且第 19 节已允许在 Research 定向检查全绿时记录该基线。

```text
A. Research R0–R6 Complete，可以形成稳定 checkpoint。
```

尚未进入本阶段的内容：File Assistant F0+、MarkItDown、Docling、PDF/DOCX/XLSX/PPTX 与
Gotenberg；这些保持为下一阶段，不应回填到本次 Research runtime。

验收结束后已删除含测试密码的 `/tmp` 状态文件，并删除精确隔离数据库
`hpagent_research_acceptance`；主开发库中的两个真实 completed Run 与备份文件保留作为验收证据。
生产 hpagent worker 已恢复，Hindsight 与 Orchestration Worker 均完成初始化。
