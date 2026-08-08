#!/usr/bin/env bash
# =============================================================================
# HpAgent Web Gateway smoke —— G-T01 / G-T02 / G-T03 / G-T04
#
# 验证生产网关组合（React build + Nginx + /api 代理 + SSE 不缓存），
# 而不是单个 Python module。可本地运行，也可在 CI（需 Docker）运行。
#
# 用法:
#   ./scripts/gateway-smoke.sh
# 环境变量:
#   GATEWAY_IMAGE  使用预构建镜像名（跳过 docker build）
#   GATEWAY_PORT   网关暴露的宿主机端口（默认随机可用端口）
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT/web"
GATEWAY_IMAGE="${GATEWAY_IMAGE:-hpagent-web-gateway:smoke}"
GATEWAY_PORT="${GATEWAY_PORT:-}"
DIST_DIR="$WEB_DIR/dist"

STUB_PID=""
GW_CONTAINER=""

cleanup() {
  if [[ -n "$GW_CONTAINER" ]]; then
    docker rm -f "$GW_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [[ -n "$STUB_PID" ]]; then
    kill "$STUB_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# ── 0. 前置检查 ──
command -v docker >/dev/null 2>&1 || { echo "FATAL: docker is required"; exit 1; }

# ── 1. G-T01: production frontend build ──
if [[ ! -f "$DIST_DIR/index.html" ]]; then
  echo "[gateway-smoke] building frontend (no dist found)..."
  (cd "$WEB_DIR" && npm ci && npm run build)
fi

# ── 2. 构建网关镜像（验证 web/Dockerfile 多阶段构建）──
if ! docker image inspect "$GATEWAY_IMAGE" >/dev/null 2>&1; then
  echo "[gateway-smoke] building gateway image ($GATEWAY_IMAGE)..."
  docker build -t "$GATEWAY_IMAGE" "$WEB_DIR"
fi

# ── 3. 启动 stub API（宿主机 0.0.0.0:8080，模拟 hpagent-api）──
STUB_SCRIPT="$(mktemp)"
cat > "$STUB_SCRIPT" <<'PYEOF'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/v1/runs/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b"data: {\"type\":\"ping\"}\n\n")
            self.wfile.flush()
            return
        if self.path.startswith("/api/"):
            body = json.dumps({"path": self.path, "ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()
    def do_POST(self):
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()
    def log_message(self, *a):
        pass

HTTPServer(("0.0.0.0", 8080), H).serve_forever()
PYEOF
python3 "$STUB_SCRIPT" &
STUB_PID=$!
sleep 1

# ── 4. 启动网关容器（hpagent-api → host-gateway）──
if [[ -z "$GATEWAY_PORT" ]]; then
  # 找一个可用宿主机端口
  GATEWAY_PORT="$(
    python3 - <<'PYEOF'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PYEOF
  )"
fi
GW_CONTAINER="gw-smoke-$$"
docker run -d --name "$GW_CONTAINER" \
  --add-host hpagent-api:host-gateway \
  -p "$GATEWAY_PORT:80" \
  "$GATEWAY_IMAGE" >/dev/null
sleep 2

fail() { echo "[gateway-smoke] FAIL: $1"; exit 1; }

# ── 5. G-T02: SPA fallback ──
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$GATEWAY_PORT/some/spa/deep-route")"
[[ "$code" == "200" ]] || fail "SPA fallback expected 200, got $code"
curl -s "http://127.0.0.1:$GATEWAY_PORT/some/spa/deep-route" | grep -q "root" \
  || fail "SPA fallback did not serve index.html"
echo "[gateway-smoke] PASS G-T02 SPA fallback"

# ── 6. G-T03: /api reverse proxy ──
body="$(curl -s "http://127.0.0.1:$GATEWAY_PORT/api/v1/me")"
echo "$body" | grep -q '"path": "/api/v1/me"' \
  || fail "/api proxy did not reach upstream: $body"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$GATEWAY_PORT/auth/login")"
[[ "$code" == "303" ]] || fail "/auth proxy expected 303, got $code"
echo "[gateway-smoke] PASS G-T03 /api+/auth proxy"

# ── 7. G-T04: SSE 不 buffering ──
headers="$(curl -s -N -D - -o /tmp/gw-sse-body-$$ "http://127.0.0.1:$GATEWAY_PORT/api/v1/runs/r1/events")"
echo "$headers" | grep -qi "x-accel-buffering: no" \
  || fail "SSE route missing X-Accel-Buffering: no"
grep -q '"type":"ping"' "/tmp/gw-sse-body-$$" \
  || fail "SSE event body not streamed"
echo "[gateway-smoke] PASS G-T04 SSE no buffering"

echo "[gateway-smoke] ALL PASSED"
