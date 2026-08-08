#!/usr/bin/env bash
# =============================================================================
# HpAgent backup —— Phase G G-06（docs/operations/web-release.md §3.3）
#
# 最小备份集合（§23）：
#   A. App PostgreSQL（accounts/identity_bindings/conversations/messages/runs/
#      sessions/outbox_events/workflow_executions）→ pg_dump
#   B. Workspace（.data/workspace，用户实际文件 + Git workspace）
#   C. Hindsight PostgreSQL（长期记忆）→ pg_dump
#   D. Temporal：重大升级前手工记录 volume 恢复方式（本脚本不强制）
#   E. Redis：不备份（transient state）
#
# 用法:
#   ./scripts/backup.sh              # 输出到 ./backups/
#   BACKUP_DIR=... ./scripts/backup.sh
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
echo "[backup] 提示：重大升级前还应记录 Temporal 的 volume/DB 恢复方式（§23-D）。"
