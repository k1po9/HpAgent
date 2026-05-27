#!/usr/bin/env bash
# clear-hindsight.sh —— 清除 Hindsight 中所有存储的记忆
#
# 原理:
#   Hindsight 将所有记忆存储在 PostgreSQL (pgvector) 中，bank 为 "hpagent"。
#   本脚本优先通过 Hindsight REST API 删除 bank（优雅方式），
#   若 API 不可用则直接操作 PostgreSQL 清空所有表（强力方式）。
#
# 用法:
#   ./scripts/clear-hindsight.sh            # 清除所有记忆
#   ./scripts/clear-hindsight.sh --db       # 强制使用 PostgreSQL 直接清理
#   ./scripts/clear-hindsight.sh --dry-run  # 仅显示将要执行的操作，不实际执行
#
# 注意:
#   - 清除后 HpAgent 会在下次 retain 时自动重建 bank
#   - 此操作不可逆，请谨慎使用

set -euo pipefail

COMPOSE_FILE="/root/workspace/HpAgent/docker-compose.yaml"
BANK_ID="${HINDSIGHT_BANK_ID:-hpagent}"
HINDSIGHT_HOST="${HINDSIGHT_HOST:-localhost:8001}"
POSTGRES_SERVICE="hindsight-postgres"
POSTGRES_DB="hindsight"
POSTGRES_USER="hindsight"

DRY_RUN=false
FORCE_DB=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --db)      FORCE_DB=true ;;
        --help|-h)
            echo "Usage: $0 [--db] [--dry-run]"
            echo ""
            echo "Options:"
            echo "  --db       直接操作 PostgreSQL 清空所有表（跳过 API）"
            echo "  --dry-run  仅显示操作，不实际执行"
            exit 0
            ;;
    esac
done

# ──────────────────────────────────────────────
# 显示当前状态
# ──────────────────────────────────────────────
show_status() {
    echo "=============================================="
    echo "  Hindsight Memory Cleaner"
    echo "=============================================="
    echo "  Bank ID:      $BANK_ID"
    echo "  API endpoint: http://${HINDSIGHT_HOST}"
    echo "  PG service:   $POSTGRES_SERVICE"
    echo "  Dry run:      $DRY_RUN"
    echo "  Force DB:     $FORCE_DB"
    echo "=============================================="
    echo ""
}

# ──────────────────────────────────────────────
# 方案 A: 通过 Hindsight REST API 删除 bank
# ──────────────────────────────────────────────
clear_via_api() {
    local url="http://${HINDSIGHT_HOST}/v1/default/banks/${BANK_ID}"

    echo "[INFO] Deleting bank '${BANK_ID}' via Hindsight API..."
    echo "[INFO] DELETE ${url}"

    if $DRY_RUN; then
        echo "[DRY-RUN] Would delete bank via API."
        return 0
    fi

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$url" --connect-timeout 5)

    case "$http_code" in
        200|204)
            echo "[OK] Bank '${BANK_ID}' deleted successfully (HTTP ${http_code})."
            return 0
            ;;
        404)
            echo "[OK] Bank '${BANK_ID}' does not exist (HTTP 404) — nothing to clear."
            return 0
            ;;
        *)
            echo "[WARN] API returned HTTP ${http_code}, falling back to PostgreSQL."
            return 1
            ;;
    esac
}

# ──────────────────────────────────────────────
# 方案 B: 直接清空 PostgreSQL 中所有表
# ──────────────────────────────────────────────
clear_via_postgres() {
    echo "[INFO] Clearing all tables in PostgreSQL database '${POSTGRES_DB}'..."

    # 获取所有用户表名
    local tables
    tables=$(docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A -c \
        "SELECT tablename FROM pg_tables WHERE schemaname='public';" 2>/dev/null || true)

    if [[ -z "$tables" ]]; then
        echo "[OK] No tables found in public schema — nothing to clear."
        return 0
    fi

    echo "[INFO] Found tables:"
    echo "$tables" | while read -r t; do echo "         - $t"; done

    if $DRY_RUN; then
        echo "[DRY-RUN] Would truncate $(echo "$tables" | wc -l) table(s)."
        return 0
    fi

    # 在单个事务中清空所有表（TRUNCATE 保留表结构，CASCADE 处理外键依赖）
    local truncate_sql="TRUNCATE TABLE $(echo "$tables" | paste -sd ',' -) CASCADE;"
    docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$truncate_sql" 2>&1

    echo "[OK] All tables truncated."
    return 0
}

# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────
main() {
    show_status

    # 确认操作
    if ! $DRY_RUN; then
        echo "⚠️  此操作将删除 Hindsight 中所有记忆，不可逆！"
        echo ""
        read -r -p "确认清除？输入 'yes' 继续: " confirm
        if [[ "$confirm" != "yes" ]]; then
            echo "已取消。"
            exit 0
        fi
        echo ""
    fi

    if $FORCE_DB; then
        clear_via_postgres
    else
        if ! clear_via_api; then
            clear_via_postgres
        fi
    fi

    echo ""
    echo "[DONE] Hindsight 记忆已清除。下一次 retain 操作将自动重建 bank。"
}

main
