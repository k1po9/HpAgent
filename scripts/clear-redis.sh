#!/usr/bin/env bash
# clear-redis.sh —— 清空 Redis 会话数据
#
# 用法:
#   ./scripts/clear-redis.sh
#
# Redis 存储: session 元信息、events 列表、sandbox 结果缓存
# 清空后: 模型丢失当前对话上下文，但长期记忆 (Hindsight) 不受影响。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yaml"
REDIS_SERVICE="redis"

echo "=============================================="
echo "  Redis Session Cleaner"
echo "=============================================="
echo "  Service: ${REDIS_SERVICE}"
echo "=============================================="
echo ""

echo "⚠️  此操作将清空 Redis 中所有数据:"
echo "   - 会话元信息 (session status / account binding)"
echo "   - 对话事件历史 (context window 来源)"
echo "   - 沙箱工具执行结果缓存"
echo ""
echo "   长期记忆 (Hindsight/pgvector) 不受影响。"
echo ""

read -r -p "确认清空 Redis？输入 'FLUSHALL' 继续: " confirm
if [[ "$confirm" != "FLUSHALL" ]]; then
    echo "已取消。"
    exit 0
fi

echo ""
echo "[INFO] 执行 FLUSHALL ..."
docker compose -f "$COMPOSE_FILE" exec -T "$REDIS_SERVICE" redis-cli FLUSHALL

echo "[OK] Redis 已清空。"
echo "[DONE] 下次发消息时 session 从零开始。"
