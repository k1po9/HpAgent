#!/usr/bin/env bash
# dev-reset.sh — 开发调试重置脚本
#
# 场景: 宿主机调试 main.py，需要清理残留状态后重新开始。
#
# 用法:
#   ./scripts/dev-reset.sh                 # 杀进程 + 清 Temporal workflow（默认）
#   ./scripts/dev-reset.sh --kill           # 只杀 HpAgent 进程
#   ./scripts/dev-reset.sh --clean          # 只清 Temporal workflow
#   ./scripts/dev-reset.sh --clean-workspace # 清 workspace 数据（accounts/repo/sessions/DB）
#   ./scripts/dev-reset.sh --clean-data     # 清所有 .data/ （workspace + sessions + logs + accounts.json）
#   ./scripts/dev-reset.sh --full           # 以上全部

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yaml"
DATA_DIR="$PROJECT_ROOT/.data"
WORKSPACE_DIR="$DATA_DIR/workspace"
TASK_QUEUE="${TEMPORAL_TASK_QUEUE:-hpagent-task-queue}"
NAMESPACE="${TEMPORAL_NAMESPACE:-default}"

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

kill_hpagent() {
    local pids
    pids=$(pgrep -f "main.py" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "[dev-reset] Killing HpAgent process(es): $pids"
        kill $pids 2>/dev/null || true
        sleep 1
        pids=$(pgrep -f "main.py" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            echo "[dev-reset] Force killing: $pids"
            kill -9 $pids 2>/dev/null || true
        fi
        echo "[dev-reset] HpAgent process stopped."
    else
        echo "[dev-reset] No running HpAgent process found."
    fi
}

clean_temporal() {
    echo "[dev-reset] Cleaning Temporal workflows on task queue: ${TASK_QUEUE} (namespace: ${NAMESPACE}) ..."

    local wf_ids
    wf_ids=$(docker compose -f "$COMPOSE_FILE" exec -T temporal \
        tctl --address localhost:7233 \
        --namespace "$NAMESPACE" \
        workflow list \
        --query "TaskQueue='${TASK_QUEUE}' AND ExecutionStatus='Running'" \
        --print_json 2>/dev/null | jq -r '.[].execution.workflowId' 2>/dev/null || true)

    if [[ -z "$wf_ids" ]]; then
        echo "[dev-reset] No running workflows found on task queue '${TASK_QUEUE}'."
        return
    fi

    local count=$(echo "$wf_ids" | wc -l)
    echo "[dev-reset] Found $count running workflow(s) to terminate:"

    for wf_id in $wf_ids; do
        echo "[dev-reset]   Terminating: $wf_id"
        docker compose -f "$COMPOSE_FILE" exec -T temporal \
            tctl --address localhost:7233 \
            --namespace "$NAMESPACE" \
            workflow terminate \
            --workflow_id "$wf_id" \
            --reason "dev reset" \
            2>/dev/null || echo "[dev-reset]   (already completed or not found)"
    done

    echo "[dev-reset] Temporal cleanup done. Terminated $count workflow(s)."
}

clean_workspace() {
    echo "[dev-reset] Cleaning workspace data ..."
    echo "[dev-reset]   Removing: $WORKSPACE_DIR"

    if [[ -d "$WORKSPACE_DIR" ]]; then
        # 列出将要删除的 account 目录
        for account_dir in "$WORKSPACE_DIR"/*/; do
            [[ -d "$account_dir" ]] || continue
            local aid
            aid=$(basename "$account_dir")
            echo "[dev-reset]     account: $aid"
            if [[ -d "$account_dir/repo/.git" ]]; then
                echo "[dev-reset]       - git repo (branches: $(cd "$account_dir/repo" && git branch 2>/dev/null | wc -l))"
            fi
            if [[ -d "$account_dir/sessions" ]]; then
                echo "[dev-reset]       - sessions: $(ls "$account_dir/sessions" 2>/dev/null | wc -l)"
            fi
        done

        rm -rf "$WORKSPACE_DIR"
        echo "[dev-reset]   Workspace data removed."
    else
        echo "[dev-reset]   Workspace dir does not exist, skipping."
    fi
}

clean_all_data() {
    echo "[dev-reset] Cleaning all .data/ contents ..."
    echo "[dev-reset]   Removing: $DATA_DIR"

    if [[ -d "$DATA_DIR" ]]; then
        # 保留目录结构但清空内容
        rm -rf "$DATA_DIR"/accounts.json 2>/dev/null || true
        rm -rf "$DATA_DIR"/workspace 2>/dev/null || true
        rm -rf "$DATA_DIR"/sessions 2>/dev/null || true
        rm -rf "$DATA_DIR"/logs 2>/dev/null || true
        echo "[dev-reset]   All .data/ contents removed (napcat/ preserved)."
    else
        echo "[dev-reset]   .data/ dir does not exist, skipping."
    fi
}

# ──────────────────────────────────────────────
# 帮助
# ──────────────────────────────────────────────

usage() {
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Options:"
    echo "  (none)             Kill process + clean Temporal workflows (default)"
    echo "  --kill             只杀 HpAgent 进程"
    echo "  --clean            只清 Temporal 残留 workflow"
    echo "  --clean-workspace  清 workspace 数据（accounts/repo/sessions/DB）"
    echo "  --clean-data       清所有 .data/ （workspace + sessions + logs + accounts.json）"
    echo "  --full             以上全部"
    echo ""
    echo "Data paths:"
    echo "  project:  $PROJECT_ROOT"
    echo "  .data:    $DATA_DIR"
    echo "  compose:  $COMPOSE_FILE"
    exit 0
}

# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

case "${1:-}" in
    --help|-h)
        usage
        ;;
    --kill)
        kill_hpagent
        ;;
    --clean)
        clean_temporal
        ;;
    --clean-workspace)
        kill_hpagent
        clean_workspace
        echo ""
        echo "[dev-reset] Workspace cleaned. Next start will re-init accounts/repos from scratch."
        ;;
    --clean-data)
        kill_hpagent
        clean_all_data
        echo ""
        echo "[dev-reset] .data/ cleaned (napcat/ preserved). Next start will re-init everything."
        ;;
    --full)
        kill_hpagent
        clean_temporal
        clean_all_data
        echo ""
        echo "[dev-reset] Full reset complete."
        echo "[dev-reset] Next start: fresh Temporal state + fresh .data/ + fresh workspace."
        ;;
    *)
        kill_hpagent
        clean_temporal
        echo ""
        echo "[dev-reset] All clean. You can now start debugging."
        echo "[dev-reset] Tip: use '$0 --clean-workspace' to also reset workspace/repos."
        echo "[dev-reset] Tip: set TEMPORAL_TASK_QUEUE=hpagent-task-queue-\$(date +%s) for isolated debug sessions."
        ;;
esac
