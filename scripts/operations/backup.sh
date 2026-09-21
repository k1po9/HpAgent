#!/usr/bin/env bash
# =============================================================================
# HpAgent application-state backup.
#
# 当前脚本覆盖的状态：
#   A. App PostgreSQL（accounts/identity_bindings/conversations/messages/runs/
#      sessions/outbox_events/workflow_executions）→ pg_dump
#   B. Workspace（.data/workspace，用户实际文件 + Git workspace）
#   C. Hindsight PostgreSQL（长期记忆）→ pg_dump
#   D. Temporal：需在部署层另行备份 PostgreSQL/volume（本脚本不包含）
#   E. Redis：不备份（transient state）
#
# 用法:
#   ./scripts/operations/backup.sh
#   BACKUP_DIR=... ./scripts/operations/backup.sh
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

OUT="${BACKUP_DIR:-./backups}"
mkdir -p "$OUT"
stamp="$(date +%F-%H%M%S)"

echo "[backup] app-postgres (canonical state)..."
docker compose exec -T app-postgres \
  pg_dump -U hpagent_migrate -d hpagent -F c > "$OUT/app-$stamp.dump"

echo "[backup] hindsight-postgres (long-term memory)..."
docker compose exec -T hindsight-postgres \
  pg_dump -U hindsight -d hindsight -F c > "$OUT/hindsight-$stamp.dump"

echo "[backup] workspace (.data/workspace)..."
tar czf "$OUT/workspace-$stamp.tar.gz" .data/workspace

echo "[backup] done → $OUT (stamp=$stamp)"
echo "[backup] 提示：Temporal PostgreSQL/volume 和 File Store 需要在部署层另行备份。"
