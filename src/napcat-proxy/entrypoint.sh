#!/bin/bash
# ============================================================
# NapCat 透明代理入口 — iptables REDIRECT → redsocks → SOCKS5
# ============================================================
# 环境变量：
#   PROXY_OPTIONAL=true   代理不可达时走直连（默认 fail-closed）
#   PROXY_HOST            代理地址（默认 127.0.0.1）
#   PROXY_PORT            代理端口（默认 1080）
#   REDSOCKS_PORT         redsocks 本地监听端口（默认 12345）
# ============================================================
set -e

PROXY_HOST="${PROXY_HOST:-127.0.0.1}"
PROXY_PORT="${PROXY_PORT:-1080}"
PROXY_OPTIONAL="${PROXY_OPTIONAL:-false}"
REDSOCKS_PORT="${REDSOCKS_PORT:-12345}"
USE_PROXY=true

# ── 代理可达性检查 ──
if ! nc -z "$PROXY_HOST" "$PROXY_PORT" 2>/dev/null; then
    if [ "$PROXY_OPTIONAL" = "true" ]; then
        echo "[napcat-proxy] WARN: SOCKS5 proxy ${PROXY_HOST}:${PROXY_PORT} 不可达，走直连"
        USE_PROXY=false
    else
        echo "[napcat-proxy] FATAL: SOCKS5 proxy ${PROXY_HOST}:${PROXY_PORT} 不可达，拒绝启动"
        echo "[napcat-proxy] 设置 PROXY_OPTIONAL=true 可在代理不可达时降级为直连"
        exit 1
    fi
fi

# ── 包装 /opt/QQ/qq 注入 GPU 禁用参数 ──
if [ -x /opt/QQ/qq ] && [ ! -L /opt/QQ/qq ] && [ ! -e /opt/QQ/qq.bin ]; then
    mv /opt/QQ/qq /opt/QQ/qq.bin
    cat > /opt/QQ/qq << 'WRAPPER'
#!/bin/bash
exec /opt/QQ/qq.bin --disable-gpu --disable-gpu-sandbox --disable-software-rasterizer "$@"
WRAPPER
    chmod +x /opt/QQ/qq
    echo "[napcat-proxy] QQ binary wrapped with --disable-gpu flags"
fi

# ── 透明代理设置（iptables REDIRECT + redsocks） ──
setup_transparent_proxy() {
    # 用 uid-owner 限定只代理 napcat 用户 (UID 1000) 的流量
    # 这样 host 模式下的其他服务不受影响
    iptables -t nat -A OUTPUT \
        -m owner --uid-owner 1000 \
        -p tcp \
        -d 127.0.0.0/8 \
        -j RETURN 2>/dev/null || true

    iptables -t nat -A OUTPUT \
        -m owner --uid-owner 1000 \
        -p tcp \
        -j REDIRECT --to-port "$REDSOCKS_PORT" 2>/dev/null || true

    echo "[napcat-proxy] iptables REDIRECT rules added (UID 1000 → port ${REDSOCKS_PORT})"
}

cleanup_transparent_proxy() {
    echo "[napcat-proxy] cleaning up iptables rules..."
    iptables -t nat -D OUTPUT \
        -m owner --uid-owner 1000 \
        -p tcp \
        -d 127.0.0.0/8 \
        -j RETURN 2>/dev/null || true
    iptables -t nat -D OUTPUT \
        -m owner --uid-owner 1000 \
        -p tcp \
        -j REDIRECT --to-port "$REDSOCKS_PORT" 2>/dev/null || true
    echo "[napcat-proxy] iptables rules cleaned up"
}

if [ "$USE_PROXY" = "true" ]; then
    # 检查 iptables owner 模块是否可用
    if ! iptables -t nat -A OUTPUT -m owner --uid-owner 1000 -p tcp -j RETURN 2>/dev/null; then
        echo "[napcat-proxy] WARN: iptables owner 模块不可用，回退到 proxychains"
        iptables -t nat -D OUTPUT -m owner --uid-owner 1000 -p tcp -j RETURN 2>/dev/null || true
        exec proxychains4 -q -f /etc/proxychains4.conf bash /app/entrypoint.sh "$@"
    fi
    # 撤掉测试规则
    iptables -t nat -D OUTPUT -m owner --uid-owner 1000 -p tcp -j RETURN 2>/dev/null || true

    # 生成 redsocks 配置
    sed -i "s/ip = 127.0.0.1;/ip = ${PROXY_HOST};/" /etc/redsocks.conf
    sed -i "s/port = 1080;/port = ${PROXY_PORT};/" /etc/redsocks.conf
    sed -i "s/local_port = 12345;/local_port = ${REDSOCKS_PORT};/" /etc/redsocks.conf

    # 启动 redsocks（后台运行）
    echo "[napcat-proxy] starting redsocks (${PROXY_HOST}:${PROXY_PORT} → localhost:${REDSOCKS_PORT})..."
    redsocks -c /etc/redsocks.conf &
    REDSOCKS_PID=$!
    sleep 1

    if ! kill -0 "$REDSOCKS_PID" 2>/dev/null; then
        echo "[napcat-proxy] FATAL: redsocks 启动失败"
        exit 1
    fi

    # 设置 iptables 重定向规则
    setup_transparent_proxy

    # 退出时清理
    trap 'cleanup_transparent_proxy; kill $REDSOCKS_PID 2>/dev/null; echo "[napcat-proxy] shutdown complete"' EXIT

    echo "[napcat-proxy] 透明代理已启用（iptables REDIRECT → redsocks → SOCKS5）"
else
    echo "[napcat-proxy] 跳过代理，QQ 直连..."
fi

exec bash /app/entrypoint.sh "$@"
