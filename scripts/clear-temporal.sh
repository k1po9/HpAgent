#!/usr/bin/env bash
# clear-temporal.sh —— 清空 Temporal workflow 历史
#
# 用法:
#   ./scripts/clear-temporal.sh
#
# Temporal 在 PostgreSQL 中持久化所有 workflow 执行历史。
# 开发调试时残留的失败 workflow 会阻塞同名新 workflow 的创建。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yaml"
TEMPORAL_SERVICE="temporal"
NAMESPACE="${TEMPORAL_NAMESPACE:-default}"
TASK_QUEUE="${TEMPORAL_TASK_QUEUE:-hpagent-task-queue}"

echo "=============================================="
echo "  Temporal Workflow Cleaner"
echo "=============================================="
echo "  Namespace:  ${NAMESPACE}"
echo "  Task Queue: ${TASK_QUEUE}"
echo "=============================================="
echo ""

# ── 终止运行中的 workflow ──
echo "[1/2] 终止运行中的 workflow ..."

wf_json=$(docker compose -f "$COMPOSE_FILE" exec -T "$TEMPORAL_SERVICE" \
    tctl --address localhost:7233 \
    --namespace "$NAMESPACE" \
    workflow list \
    --query "TaskQueue='${TASK_QUEUE}' AND ExecutionStatus='Running'" \
    --print_json 2>/dev/null || true)

if [[ -z "$wf_json" || "$wf_json" == "[]" || "$wf_json" == "null" ]]; then
    echo "      没有运行中的 workflow。"
else
    echo "$wf_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
if not data:
    print('      (空)')
else:
    for w in data:
        wf_id = w.get('execution', {}).get('workflowId', '?')
        run_id = w.get('execution', {}).get('runId', '?')
        print(f'      {wf_id}')
" 2>/dev/null

    echo ""
    echo "⚠️  将终止以上所有运行中的 workflow。"
    echo "   之后同名 session 可以重新创建。"
    echo ""

    read -r -p "确认终止？[y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "已跳过终止步骤。"
    else
        echo ""
        ids=$(echo "$wf_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for w in (data if isinstance(data, list) else []):
    wf_id = w.get('execution', {}).get('workflowId', '')
    if wf_id:
        print(wf_id)
" 2>/dev/null)

        while IFS= read -r wf_id; do
            [[ -z "$wf_id" ]] && continue
            echo "      终止: $wf_id"
            docker compose -f "$COMPOSE_FILE" exec -T "$TEMPORAL_SERVICE" \
                tctl --address localhost:7233 \
                --namespace "$NAMESPACE" \
                workflow terminate \
                --workflow_id "$wf_id" \
                --reason "dev cleanup" \
                2>/dev/null || echo "      (已完成或不存在)"
        done <<< "$ids"
        echo "      终止完成。"
    fi
fi

# ── 清空 workflow 历史（PostgreSQL） ──
echo ""
echo "[2/2] 清空 Temporal PostgreSQL 中的 workflow 历史 ..."
echo "      此操作清空 executions、history、tasks 等表。"
echo "      namespace 和 schedule 配置保留。"
echo ""

read -r -p "确认清空 Temporal 历史数据？输入 'yes' 继续: " confirm
if [[ "$confirm" != "yes" ]]; then
    echo "已跳过。"
else
    echo ""
    echo "[INFO] 清空 temporal 数据库的 executions 相关表 ..."

    # 列出 temporal 数据库中的表
    tables=$(docker compose -f "$COMPOSE_FILE" exec -T temporal-postgres \
        psql -U temporal -d temporal -t -A -c \
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT LIKE '%schema%' AND tablename NOT LIKE '%migration%';" 2>/dev/null || true)

    if [[ -n "$tables" ]]; then
        echo "[INFO] 将清空以下表:"
        echo "$tables" | while read -r t; do echo "         - $t"; done

        truncate_sql="TRUNCATE TABLE $(echo "$tables" | paste -sd ',' -) CASCADE;"
        docker compose -f "$COMPOSE_FILE" exec -T temporal-postgres \
            psql -U temporal -d temporal -c "$truncate_sql" 2>&1

        echo "[OK] 已清空。"
    else
        echo "[OK] 没有用户表，跳过。"
    fi
fi

echo ""
echo "[DONE] Temporal 清理完成。"
