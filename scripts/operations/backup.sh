#!/usr/bin/env bash
# App/Hindsight dumps and Git workspace only; Temporal/File Store require matching snapshots.
set -euo pipefail
usage() {
  cat <<'EOF'
用法：BACKUP_DIR=/absolute/private/location scripts/operations/backup.sh
      scripts/operations/backup.sh --output /absolute/private/location
读取运行中的 App/Hindsight PostgreSQL 和 .data/workspace；写仓库外受限备份。
不停止服务，不保证跨数据库/文件系统一致性；不含 Temporal 与 File Store。
每次创建独立子目录，失败保留 .incomplete 标记并非零退出。禁止输出到仓库内。
参数：--output DIR；-h/--help。依赖：Docker Compose、python3、tar。
EOF
}
OUT="${BACKUP_DIR:-}"
while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --output) [[ $# -ge 2 && -n "$2" ]] || { echo '缺少 --output DIR' >&2; exit 2; }; OUT="$2"; shift ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done
[[ "$OUT" = /* ]] || { echo '必须设置仓库外绝对路径 BACKUP_DIR 或 --output' >&2; exit 2; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$OUT")"
[[ "$OUT" != "$ROOT" && "$OUT" != "$ROOT/"* && "$OUT" != / ]] || { echo '备份输出必须在仓库之外' >&2; exit 2; }
for tool in docker tar; do command -v "$tool" >/dev/null || { echo "缺少 $tool" >&2; exit 2; }; done
[[ -d "$ROOT/.data/workspace" ]] || { echo '.data/workspace 不存在；先核实备份范围' >&2; exit 2; }
cd "$ROOT"
umask 077
mkdir -p "$OUT"
RUN_DIR="$(mktemp -d "$OUT/backup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
touch "$RUN_DIR/.incomplete"
docker compose exec -T app-postgres pg_dump -U hpagent_migrate -d hpagent -F c > "$RUN_DIR/app.dump"
docker compose exec -T hindsight-postgres pg_dump -U hindsight -d hindsight -F c > "$RUN_DIR/hindsight.dump"
tar czf "$RUN_DIR/workspace.tar.gz" .data/workspace
rm "$RUN_DIR/.incomplete"
echo "[backup] 完成：$RUN_DIR；仍须取得匹配的 Temporal/File Store 快照"
