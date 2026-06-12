#!/usr/bin/env bash
# =============================================================================
# 本地 MCP 服务器启动脚本（独立于 HpAgent）
#
# 用法:
#   ./scripts/start-mcp.sh           # 启动所有本地 MCP 服务
#   ./scripts/start-mcp.sh --status  # 查看运行状态
#   ./scripts/start-mcp.sh --stop    # 停止所有本地 MCP 服务
#
# 这些 MCP 服务器可部署在任意机器上，HpAgent 通过 Streamable HTTP 连接。
# 迁移到其他服务器时，修改 config/mcp/servers.yaml 中的 url 即可。
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
PID_FILE="$PROJECT_ROOT/.data/mcp.pids"

# ═════════════════════════════════════════════════════════════════════
# MCP 服务器注册表（追加新服务只需在此加一行）
#   格式: "名称 host:port 启动命令"
# ═════════════════════════════════════════════════════════════════════
MCP_SERVERS=(
    "akshare-one-mcp 127.0.0.1:8081 $VENV_PYTHON -m akshare_one_mcp --streamable-http --host 127.0.0.1 --port 8081"
    # 将来在此追加:
    # "another-mcp 127.0.0.1:8082 some-command --port 8082"
)

# ═════════════════════════════════════════════════════════════════════
start() {
    mkdir -p "$(dirname "$PID_FILE")"
    > "$PID_FILE"

    for entry in "${MCP_SERVERS[@]}"; do
        read -r name addr cmd <<< "$entry"

        # 检查端口是否已被占用
        port="${addr#*:}"
        if ss -tlnp 2>/dev/null | grep -q ":$port " || \
           lsof -i ":$port" 2>/dev/null | grep -q LISTEN; then
            echo "[SKIP] $name — 端口 $port 已被占用"
            continue
        fi

        echo "[MCP] $name → http://$addr/mcp"
        $cmd &
        pid=$!
        echo "$name $pid" >> "$PID_FILE"

        # 等一小会儿让服务启动
        sleep 0.5
    done

    echo ""
    echo "已启动 $(wc -l < "$PID_FILE") 个 MCP 服务器。"
    echo "PID 文件: $PID_FILE"
    echo "停止: ./scripts/start-mcp.sh --stop"
}

# ═════════════════════════════════════════════════════════════════════
stop() {
    if [ ! -f "$PID_FILE" ] || [ ! -s "$PID_FILE" ]; then
        echo "没有运行中的 MCP 服务器。"
        return
    fi

    while read -r name pid; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "[STOP] $name (PID $pid)"
            kill "$pid" 2>/dev/null || true
        fi
    done < "$PID_FILE"
    rm -f "$PID_FILE"
    echo "已停止。"
}

# ═════════════════════════════════════════════════════════════════════
status() {
    if [ ! -f "$PID_FILE" ] || [ ! -s "$PID_FILE" ]; then
        echo "没有运行中的 MCP 服务器。"
        return
    fi

    printf "%-25s %-8s %s\n" "名称" "PID" "状态"
    printf "%-25s %-8s %s\n" "----" "---" "----"
    while read -r name pid; do
        if kill -0 "$pid" 2>/dev/null; then
            printf "%-25s %-8s %s\n" "$name" "$pid" "运行中"
        else
            printf "%-25s %-8s %s\n" "$name" "$pid" "已退出"
        fi
    done < "$PID_FILE"
}

# ═════════════════════════════════════════════════════════════════════
case "${1:-start}" in
    --stop|-s)    stop ;;
    --status|-st) status ;;
    start|--start) start ;;
    *) echo "用法: $0 [start|--stop|--status]" ;;
esac
