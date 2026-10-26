#!/usr/bin/env bash
# Full cold backup. Never stops/starts services or modifies source data.
set -euo pipefail
usage() {
  cat <<'EOF'
用法：BACKUP_DIR=/absolute/private/location scripts/operations/backup.sh
      scripts/operations/backup.sh --output /absolute/private/location
维护窗口：先在受控入口停止流量，再停止本 Compose project 全部容器（包括基础设施）。
脚本要求容器已存在且全部停止；只读归档全部已挂载命名卷、.data、配置及版本信息。
不停止/启动服务；不调用模型或 QQ；失败留下 .incomplete；输出必须在仓库外。
使用本地 postgres:16-alpine 的 tar（--pull never，网络禁用），不下载辅助镜像。
务必使用部署时相同的 COMPOSE_FILE / COMPOSE_PROJECT_NAME / 环境配置。
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
for tool in docker tar git sha256sum python3; do command -v "$tool" >/dev/null || { echo "缺少 $tool" >&2; exit 2; }; done
[[ -d "$ROOT/.data" ]] || { echo '.data 不存在；先核实备份范围' >&2; exit 2; }
[[ ! -L "$ROOT/.data" ]] || { echo ".data 是符号链接；须另行核实实际数据备份范围" >&2; exit 2; }
cd "$ROOT"
umask 077
mkdir -p "$OUT"
RUN_DIR="$(mktemp -d "$OUT/backup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
touch "$RUN_DIR/.incomplete"
# Inspect only state/mounts/image IDs, never container environment or credentials.
mapfile -t CONTAINERS < <(docker compose --profile web-prod --profile qq ps -aq)
((${#CONTAINERS[@]})) || { echo '找不到部署容器；拒绝生成不完整备份' >&2; exit 2; }
for id in "${CONTAINERS[@]}"; do
  [[ "$(docker inspect --format '{{.State.Status}}' "$id")" == exited ]] || { echo '所有部署容器必须已停止（exited）' >&2; exit 2; }
done
for service in app-postgres temporal-postgres hindsight-postgres redis hpagent hpagent-api hpagent-document-worker web-gateway napcat; do
  [[ -n "$(docker compose --profile web-prod --profile qq ps -aq "$service")" ]] || { echo "缺少部署容器：$service" >&2; exit 2; }
done
for id in "${CONTAINERS[@]}"; do
  docker inspect --format '{{json .Mounts}}' "$id"
done > "$RUN_DIR/mounts.jsonl"
python3 - "$RUN_DIR" <<'PY'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
mounts=[m for line in (root/'mounts.jsonl').read_text().splitlines() for m in json.loads(line)]
volumes=sorted({m['Name'] for m in mounts if m['Type']=='volume'})
required={'/var/lib/postgresql/data','/data','/var/lib/hpagent/file-store','/var/lib/hpagent/file-runs','/var/lib/hpagent/document-runs'}
if not required.issubset({m['Destination'] for m in mounts if m['Type']=='volume'}):
    raise SystemExit('持久卷挂载不完整，拒绝继续')
(root/'volumes.txt').write_text('\n'.join(volumes)+'\n')
PY
while IFS= read -r volume; do
  # Source mounts are read-only. No project volume is attached writable.
  docker run --rm --pull never --network none --read-only --entrypoint tar \
    --mount "type=volume,src=$volume,dst=/source,readonly" \
    postgres:16-alpine -C /source -czf - . > "$RUN_DIR/volume-$volume.tar.gz"
done < "$RUN_DIR/volumes.txt"
# Includes private NapCat state and logs: protect/encrypt this entire backup set.
tar czf "$RUN_DIR/data.tar.gz" .data
configuration=(config docker-compose.yaml docker-compose.beta.yaml)
for overlay in docker-compose.pgroonga.yaml docker-compose.pgroonga.enabled.yaml docker-compose.pgroonga.recovery.yaml; do
  [[ ! -f "$overlay" ]] || configuration+=("$overlay")
done
tar czf "$RUN_DIR/configuration.tar.gz" "${configuration[@]}"
# Secret file is managed separately; do not render Compose config (would expand secrets).
git rev-parse HEAD > "$RUN_DIR/git-commit.txt"
git status --porcelain > "$RUN_DIR/git-status.txt"
for id in "${CONTAINERS[@]}"; do
  docker inspect --format '{{.Name}} {{.Image}}' "$id"
done > "$RUN_DIR/images.txt"
(cd "$RUN_DIR" && sha256sum -- *.tar.gz *.txt mounts.jsonl > SHA256SUMS)
rm "$RUN_DIR/.incomplete"
echo "[backup] 冷备归档完成：$RUN_DIR；需校验、加密异机同步及隔离恢复演练"
