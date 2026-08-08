#!/usr/bin/env bash
# =============================================================================
# HpAgent production compose smoke —— Phase G G-08（§19 / §32 验收）
#
# 在真正部署组合上跑通整条链路，而不是单个 Python module：
#   docker compose build → up → migration → healthy → login → create Conversation
#   → send one message → Run reaches completed → GET history contains final answer
#
# 不验证模型回答质量，只验证“整条链路能够工作”（§19）。
#
# 前置条件（真实生产组合）：
#   - 已配置模型 API key（MINIMAX_API_KEY / SILICONFLOW_API_KEY 等）
#   - 已运行 scripts/bootstrap_identity.py（WEB_UNIFIED_ACCOUNT_ENABLED=true 时）
#   - WEB_CREDENTIALS_JSON 含 smoke 用户，或使用环境变量覆盖
#   - HPAGENT_ENV=production 时不得启用 WEB_FAKE_EXECUTOR_ENABLED
#
# 用法:
#   ./scripts/release-smoke.sh
# 环境变量:
#   SMOKE_USER / SMOKE_PASSWORD   登录凭证（默认 admin / smoke-password）
#   WEB_GATEWAY_URL               网关地址（默认 http://127.0.0.1:${WEB_GATEWAY_PORT:-80}）
#   SMOKE_PROFILE                 compose profile（默认 web）
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GATEWAY_URL="${WEB_GATEWAY_URL:-http://127.0.0.1:${WEB_GATEWAY_PORT:-80}}"
SMOKE_USER="${SMOKE_USER:-admin}"
SMOKE_PASSWORD="${SMOKE_PASSWORD:-smoke-password}"
PROFILE="${SMOKE_PROFILE:-web}"
JAR="$(mktemp)"
RUN_DIR="$(mktemp -d)"

cleanup() { rm -f "$JAR"; rm -rf "$RUN_DIR"; }
trap cleanup EXIT

fail() { echo "[release-smoke] FAIL: $1"; exit 1; }
pass() { echo "[release-smoke] PASS: $1"; }

# ── 0. 前置检查 ──
command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"

# ── 1. 构建 + 启动 ──
echo "[release-smoke] docker compose build (profile=$PROFILE)..."
docker compose --profile "$PROFILE" build
echo "[release-smoke] docker compose up -d ..."
docker compose --profile "$PROFILE" up -d

# ── 2. 等待基础设施 healthy ──
wait_healthy() { # name
  local svc="$1" i=0
  until [[ "$(docker inspect --format '{{.State.Health.Status}}' "$svc" 2>/dev/null)" == "healthy" ]]; do
    i=$((i + 1))
    if (( i > 60 )); then fail "service $svc not healthy after 120s"; fi
    sleep 2
  done
  pass "service healthy: $svc"
}

for svc in \
  "$(docker compose ps -q app-postgres | xargs docker inspect --format '{{.Name}}' 2>/dev/null)" \
  redis temporal hindsight hpagent-api web-gateway; do
  # 只在服务确实存在于 compose 时等待
  if docker compose ps --services | grep -qx "${svc#/}"; then
    wait_healthy "$(docker compose ps -q "$svc" 2>/dev/null | head -1)"
  fi
done

# hpagent Worker：无独立 HTTP health，用进程运行 + 启动日志判断（§15）
if ! docker compose ps --services | grep -qx hpagent; then
  fail "hpagent Worker service not present in compose"
fi
WORKER_CID="$(docker compose ps -q hpagent)"
[[ -n "$WORKER_CID" ]] || fail "hpagent Worker not running"
sleep 3
pass "hpagent Worker process running ($WORKER_CID)"

# ── 3. Gateway 可达 ──
code="$(curl -s -o /dev/null -w '%{http_code}' "$GATEWAY_URL/")"
[[ "$code" == "200" ]] || fail "gateway root expected 200, got $code"
pass "gateway serves SPA at $GATEWAY_URL"

# ── 4. 登录 → 会话 → CSRF ──
code="$(curl -s -o /dev/null -w '%{http_code}' -c "$JAR" -b "$JAR" \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d "{\"username\":\"$SMOKE_USER\",\"password\":\"$SMOKE_PASSWORD\",\"return_to\":\"/\"}" \
  "$GATEWAY_URL/auth/login")"
[[ "$code" == "303" ]] || fail "login expected 303, got $code (check SMOKE_USER/SMOKE_PASSWORD and WEB_CREDENTIALS_JSON)"
pass "login (303) -> $SMOKE_USER"

ME="$(curl -s -b "$JAR" -c "$JAR" "$GATEWAY_URL/api/v1/me")"
CSRF="$(echo "$ME" | jq -r '.csrf_token // empty')"
ACCOUNT="$(echo "$ME" | jq -r '.account.account_id // empty')"
[[ -n "$CSRF" ]] || fail "no csrf_token from /api/v1/me: $ME"
[[ -n "$ACCOUNT" ]] || fail "no account from /api/v1/me: $ME"
pass "session confirmed, account=$ACCOUNT"

# ── 5. 创建 Conversation ──
IDEM1="$(cat /proc/sys/kernel/random/uuid)"
CONV="$(curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H "Idempotency-Key: $IDEM1" \
  -H 'Content-Type: application/json' \
  -d '{"title":"release-smoke"}' \
  "$GATEWAY_URL/api/v1/conversations")"
CONV_ID="$(echo "$CONV" | jq -r '.conversation.conversation_id // empty')"
[[ -n "$CONV_ID" ]] || fail "create conversation failed: $CONV"
pass "conversation created: $CONV_ID"

# ── 6. 发送一条消息 ──
IDEM2="$(cat /proc/sys/kernel/random/uuid)"
SEND="$(curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H "Idempotency-Key: $IDEM2" \
  -H 'Content-Type: application/json' \
  -d '{"content":"ping — release smoke"}' \
  "$GATEWAY_URL/api/v1/conversations/$CONV_ID/messages")"
RUN_ID="$(echo "$SEND" | jq -r '.run.run_id // empty')"
[[ -n "$RUN_ID" ]] || fail "send message returned no run: $SEND"
pass "message sent, run=$RUN_ID"

# ── 7. 等待 Run 达到终态 ──
STATUS="pending"
for _ in $(seq 1 90); do
  RUN="$(curl -s -b "$JAR" "$GATEWAY_URL/api/v1/runs/$RUN_ID")"
  STATUS="$(echo "$RUN" | jq -r '.run.status // empty')"
  case "$STATUS" in
    completed) break ;;
    failed|cancelled|expired) fail "run terminal with $STATUS: $RUN" ;;
  esac
  sleep 2
done
[[ "$STATUS" == "completed" ]] || fail "run did not complete (status=$STATUS)"
pass "run completed"

# ── 8. 历史包含最终回答 ──
HIST="$(curl -s -b "$JAR" "$GATEWAY_URL/api/v1/conversations/$CONV_ID/messages?limit=100")"
ASSISTANT="$(echo "$HIST" | jq '[.messages[] | select(.role=="assistant")] | length')"
[[ "${ASSISTANT:-0}" -ge "1" ]] || fail "history has no assistant message"
pass "history contains assistant final answer (count=$ASSISTANT)"

echo ""
echo "[release-smoke] ALL PASSED — 整条链路（migration→startup→login→run→history）可用"
echo "[release-smoke] 记忆：verify .data/workspace / pg_dump backup before next release (§24)"
