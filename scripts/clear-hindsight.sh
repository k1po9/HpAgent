#!/usr/bin/env bash
# clear-hindsight.sh —— 交互式清除 Hindsight 记忆
#
# 用法:
#   ./scripts/clear-hindsight.sh
#
# 运行时通过交互菜单选择清除力度，无需传参。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yaml"
HINDSIGHT_HOST="${HINDSIGHT_HOST:-localhost:8001}"
POSTGRES_SERVICE="hindsight-postgres"
POSTGRES_DB="hindsight"
POSTGRES_USER="hindsight"

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

_banner() {
    echo "=============================================="
    echo "  Hindsight Memory Cleaner"
    echo "=============================================="
    echo "  API:    http://${HINDSIGHT_HOST}"
    echo "  DB:     ${POSTGRES_SERVICE}/${POSTGRES_DB}"
    echo "=============================================="
    echo ""
}

_list_banks() {
    echo "[INFO] 当前存在的 memory bank:"
    echo ""
    local banks
    banks=$(curl -s "http://${HINDSIGHT_HOST}/v1/default/banks" --connect-timeout 5 2>/dev/null || true)
    if [[ -z "$banks" || "$banks" == "[]" || "$banks" == "null" ]]; then
        echo "  (无)"
    else
        echo "$banks" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for b in (data if isinstance(data, list) else []):
    bid = b.get('id', b) if isinstance(b, dict) else b
    print(f'  - {bid}')
" 2>/dev/null || echo "$banks"
    fi
    echo ""
}

_delete_bank() {
    local bank_id="$1"
    echo "[INFO] 删除 bank: ${bank_id} ..."
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
        "http://${HINDSIGHT_HOST}/v1/default/banks/${bank_id}" --connect-timeout 5)

    case "$http_code" in
        200|204) echo "[OK] Bank '${bank_id}' 已删除。" ;;
        404)     echo "[OK] Bank '${bank_id}' 不存在，跳过。" ;;
        *)       echo "[FAIL] API 返回 HTTP ${http_code}" ; return 1 ;;
    esac
}

_truncate_all() {
    echo "[INFO] 清空 PostgreSQL 数据库 '${POSTGRES_DB}' 所有表 ..."

    local tables
    tables=$(docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A -c \
        "SELECT tablename FROM pg_tables WHERE schemaname='public';" 2>/dev/null || true)

    if [[ -z "$tables" ]]; then
        echo "[OK] 没有表，无需清理。"
        return 0
    fi

    echo "[INFO] 将清空以下 $(echo "$tables" | wc -l) 张表:"
    echo "$tables" | while read -r t; do echo "         - $t"; done
    echo ""

    local truncate_sql="TRUNCATE TABLE $(echo "$tables" | paste -sd ',' -) CASCADE;"
    docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$truncate_sql" 2>&1

    echo "[OK] 所有表已清空（表结构保留）。"
}

# ──────────────────────────────────────────────
# 交互菜单
# ──────────────────────────────────────────────

main() {
    _banner

    echo "请选择清除力度:"
    echo ""
    echo "  1) 清除指定用户记忆  (API 删除单个 bank，其他用户不受影响)"
    echo "  2) 清除所有用户记忆  (API 逐个删除所有 bank)"
    echo "  3) 彻底清空数据库    (PostgreSQL TRUNCATE 全部表，包括系统配置)"
    echo "  0) 取消"
    echo ""

    read -r -p "输入选项 [0-3]: " choice

    case "$choice" in
        1)
            echo ""
            _list_banks
            read -r -p "输入要清除的 bank ID (如 hpagent-u-xxxx): " bank_id
            if [[ -z "$bank_id" ]]; then
                echo "未输入 bank ID，已取消。"
                exit 0
            fi
            echo ""
            read -r -p "确认删除 bank '${bank_id}'？[y/N] " confirm
            if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
                echo "已取消。"
                exit 0
            fi
            echo ""
            _delete_bank "$bank_id"
            ;;

        2)
            echo ""
            _list_banks
            echo "⚠️  将删除以上所有 bank，不可逆！"
            echo ""
            read -r -p "确认删除所有 bank？输入 'yes' 继续: " confirm
            if [[ "$confirm" != "yes" ]]; then
                echo "已取消。"
                exit 0
            fi
            echo ""

            local banks
            banks=$(curl -s "http://${HINDSIGHT_HOST}/v1/default/banks" --connect-timeout 5 2>/dev/null || true)
            if [[ -z "$banks" || "$banks" == "[]" || "$banks" == "null" ]]; then
                echo "[OK] 没有 bank 需要删除。"
                exit 0
            fi

            local ids
            ids=$(echo "$banks" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for b in (data if isinstance(data, list) else []):
    print(b.get('id', b) if isinstance(b, dict) else b)
" 2>/dev/null)

            if [[ -z "$ids" ]]; then
                echo "[WARN] 无法解析 bank 列表，尝试 PostgreSQL 清空。"
                _truncate_all
                exit 0
            fi

            local failed=0
            while IFS= read -r bid; do
                [[ -z "$bid" ]] && continue
                _delete_bank "$bid" || ((failed++))
            done <<< "$ids"

            if [[ $failed -gt 0 ]]; then
                echo "[WARN] $failed 个 bank 删除失败。"
            fi
            ;;

        3)
            echo ""
            echo "⚠️  此操作将清空 Hindsight PostgreSQL 中所有数据！"
            echo "   包括: 所有 bank、记忆、实体关系、审计日志、系统配置"
            echo "   表结构会保留，下次启动时自动重建。"
            echo ""
            read -r -p "确认彻底清空？输入 'TRUNCATE ALL' 继续: " confirm
            if [[ "$confirm" != "TRUNCATE ALL" ]]; then
                echo "已取消。"
                exit 0
            fi
            echo ""
            _truncate_all
            ;;

        0|"")
            echo "已取消。"
            exit 0
            ;;

        *)
            echo "无效选项: $choice"
            exit 1
            ;;
    esac

    echo ""
    echo "[DONE] 完成。下次 retain 时 bank 会自动重建。"
}

main
