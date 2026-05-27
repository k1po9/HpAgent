#!/usr/bin/env bash
# dev-reset.sh — 一键清理 Temporal 中的残留 workflow，便于本地调试重启
#
# 场景: 你在 VSCode 中调试主程序，停止时 Temporal（Docker）仍保留未完成的
#       workflow task。下次 F5 启动时，Worker 会立即捡起旧消息继续处理。
#      运行此脚本可在重启前清空所有残留 workflow。
#
# 用法:
#   ./scripts/dev-reset.sh          # 清理 + 询问是否重启
#   ./scripts/dev-reset.sh --kill   # 只杀主程序，不清理 Temporal
#   ./scripts/dev-reset.sh --clean  # 只清理 Temporal，不杀主程序

set -euo pipefail

COMPOSE_FILE="/root/workspace/HpAgent/docker-compose.yaml"
TASK_QUEUE="${TEMPORAL_TASK_QUEUE:-hpagent-task-queue}"
NAMESPACE="${TEMPORAL_NAMESPACE:-default}"

# ──────────────────────────────────────────────
# 1. 杀掉正在运行的 HpAgent 主程序
# ──────────────────────────────────────────────
kill_hpagent() {
    local pids
    pids=$(pgrep -f "main.py" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "[dev-reset] Killing HpAgent process(es): $pids"
        kill $pids 2>/dev/null || true
        sleep 1
        # 如果还没死，强制 kill
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

# ──────────────────────────────────────────────
# 2. 清理 Temporal 中残留的 workflow
# ──────────────────────────────────────────────
clean_temporal() {
    echo "[dev-reset] Cleaning Temporal workflows on task queue: ${TASK_QUEUE} (namespace: ${NAMESPACE}) ..."

    # ✅ 正确：使用 --query 过滤特定任务队列的运行中工作流
    # 注意：TaskQueue 是 Temporal 内置的搜索属性，大小写敏感
    local wf_ids
    wf_ids=$(docker compose -f "$COMPOSE_FILE" exec -T temporal \
        tctl --address localhost:7233 \
        --namespace "$NAMESPACE" \
        workflow list \
        --query "TaskQueue='${TASK_QUEUE}' AND ExecutionStatus='Running'" \
        --print_json 2>/dev/null | jq -r '.[].execution.workflowId' || true)

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

# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

case "${1:-}" in
    --kill)
        kill_hpagent
        ;;
    --clean)
        clean_temporal
        ;;
    *)
        kill_hpagent
        clean_temporal
        echo ""
        echo "[dev-reset] All clean. You can now press F5 to start debugging."
        echo "[dev-reset] Tip: set TEMPORAL_TASK_QUEUE=hpagent-task-queue-\$(date +%s) in .vscode/launch.json"
        echo "[dev-reset]      to get a fresh queue per debug session without needing this script."
        ;;
esac