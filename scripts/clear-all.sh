#!/usr/bin/env bash
# clear-all.sh —— 开发环境完整重置
#
# 清空顺序: Redis → Temporal → Hindsight → Workspace
# 一次确认，全量执行。
#
# 用法:
#   ./scripts/clear-all.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yaml"
DATA_DIR="$PROJECT_ROOT/.data"
NAMESPACE="${TEMPORAL_NAMESPACE:-default}"
TASK_QUEUE="${TEMPORAL_TASK_QUEUE:-hpagent-task-queue}"

# ──────────────────────────────────────────────
# ── 先杀 HpAgent 进程（否则删了文件立刻被写回）──
HPA_PIDS=$(pgrep -f "main.py" 2>/dev/null || true)
if [[ -n "$HPA_PIDS" ]]; then
    echo "检测到运行中的 HpAgent 进程: $HPA_PIDS"
    read -r -p "先终止 HpAgent 再继续？[Y/n] " kill_confirm
    if [[ "$kill_confirm" != "n" && "$kill_confirm" != "N" ]]; then
        kill $HPA_PIDS 2>/dev/null || true
        sleep 1
        HPA_PIDS=$(pgrep -f "main.py" 2>/dev/null || true)
        if [[ -n "$HPA_PIDS" ]]; then
            kill -9 $HPA_PIDS 2>/dev/null || true
        fi
        echo "HpAgent 已终止。"
    else
        echo "已取消（请手动停止 HpAgent 后重试）。"
        exit 1
    fi
fi

echo "=============================================="
echo "  HpAgent 开发环境完整重置"
echo "=============================================="
echo ""
echo "  即将清空以下所有数据:"
echo ""
echo "  1) Redis      — 会话历史 / events / 缓存"
echo "  2) Temporal   — 运行中 workflow + 历史记录"
echo "  3) Hindsight  — 长期记忆 (PostgreSQL 全部表)"
echo "  4) Workspace  — 本地数据 (accounts/repo/sessions/DB/logs/accounts.json)"
echo ""
echo "  ⚠️  此操作不可逆！"
echo ""

read -r -p "确认全部清空？输入 'RESET ALL' 继续: " confirm
if [[ "$confirm" != "RESET ALL" ]]; then
    echo "已取消。"
    exit 0
fi

echo ""
echo "=============================================="
echo "  开始清空..."
echo "=============================================="

# ── 1. Redis ──────────────────────────────────
echo ""
echo "── [1/4] Redis ──────────────────────────"
echo "    FLUSHALL ..."
docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli FLUSHALL 2>&1
echo "    ✓ Redis 已清空"

# ── 2. Temporal ──────────────────────────────
echo ""
echo "── [2/4] Temporal ───────────────────────"
echo "    终止运行中的 workflow ..."

wf_json=$(docker compose -f "$COMPOSE_FILE" exec -T temporal \
    tctl --address localhost:7233 \
    --namespace "$NAMESPACE" \
    workflow list \
    --query "TaskQueue='${TASK_QUEUE}' AND ExecutionStatus='Running'" \
    --print_json 2>/dev/null || true)

terminated=0
if [[ -n "$wf_json" && "$wf_json" != "[]" && "$wf_json" != "null" ]]; then
    ids=$(echo "$wf_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for w in (data if isinstance(data, list) else []):
    print(w.get('execution', {}).get('workflowId', ''))
" 2>/dev/null)

    while IFS= read -r wf_id; do
        [[ -z "$wf_id" ]] && continue
        docker compose -f "$COMPOSE_FILE" exec -T temporal \
            tctl --address localhost:7233 \
            --namespace "$NAMESPACE" \
            workflow terminate \
            --workflow_id "$wf_id" \
            --reason "dev full reset" \
            2>/dev/null && ((terminated++)) || true
    done <<< "$ids"
fi
echo "    已终止 ${terminated} 个 workflow"

echo "    重建 temporal-postgres 数据卷 ..."
docker compose -f "$COMPOSE_FILE" down temporal temporal-postgres
docker volume rm -f hpagent_temporal-pgdata 2>/dev/null || true
docker compose -f "$COMPOSE_FILE" up -d temporal temporal-postgres
echo "    ✓ Temporal 已重置"

# ── 3. Hindsight ─────────────────────────────
echo ""
echo "── [3/4] Hindsight ──────────────────────"
echo "    清空 pgvector 所有表 ..."

htables=$(docker compose -f "$COMPOSE_FILE" exec -T hindsight-postgres \
    psql -U hindsight -d hindsight -t -A -c \
    "SELECT tablename FROM pg_tables WHERE schemaname='public';" 2>/dev/null || true)

if [[ -n "$htables" ]]; then
    htruncate="TRUNCATE TABLE $(echo "$htables" | paste -sd ',' -) CASCADE;"
    docker compose -f "$COMPOSE_FILE" exec -T hindsight-postgres \
        psql -U hindsight -d hindsight -c "$htruncate" > /dev/null 2>&1
fi
echo "    ✓ Hindsight 已清空"

# ── 4. Workspace 本地数据 ────────────────────
echo ""
echo "── [4/4] Workspace 本地数据 ────────────"
find "$DATA_DIR" -mindepth 1 -maxdepth 1 -not -name 'napcat' -print0 | xargs -0 rm -rf
echo "    ✓ .data/ 已清空（保留 napcat/）"

echo "    ✓ Workspace 已清空"

# ──────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  重置完成。"
echo "  已清空: Redis + Temporal + Hindsight + .data/ (保留 napcat/)"
echo "  NapCat 数据 (.data/napcat/) 已保留。"
echo "  下次启动 HpAgent 时所有状态从零开始。"
echo "=============================================="
