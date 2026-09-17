#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

tail_lines=200
follow=true
services=()

usage() {
  cat <<'EOF'
Usage: scripts/operations/logs.sh [options] [SERVICE ...]

View logs from the current Compose deployment.

Options:
  --all          all running services (default)
  --api          hpagent-api
  --worker       hpagent and hpagent-document-worker
  --qq           hpagent and napcat
  --infra        PostgreSQL, Redis, Temporal, Hindsight, SearXNG, Gotenberg
  -n, --tail N   show the most recent N lines (default: 200)
  -f, --follow   follow log output (default)
  --no-follow    print existing output and exit
  -h, --help     show this help

Examples:
  scripts/operations/logs.sh --all
  scripts/operations/logs.sh --api --tail 100 --no-follow
  scripts/operations/logs.sh --worker
  scripts/operations/logs.sh temporal app-postgres
EOF
}

while (($#)); do
  case "$1" in
    --all) services=() ;;
    --api) services+=(hpagent-api) ;;
    --worker) services+=(hpagent hpagent-document-worker) ;;
    --qq) services+=(hpagent napcat) ;;
    --infra)
      services+=(app-postgres redis temporal temporal-postgres hindsight hindsight-postgres searxng gotenberg)
      ;;
    -n|--tail)
      [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || { echo "--tail requires a non-negative integer" >&2; exit 2; }
      tail_lines="$2"
      shift
      ;;
    -f|--follow) follow=true ;;
    --no-follow) follow=false ;;
    -h|--help) usage; exit 0 ;;
    --) shift; services+=("$@"); break ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) services+=("$1") ;;
  esac
  shift
done

compose=(docker compose --profile agent --profile web --profile web-prod --profile qq --profile tools)
args=(logs --tail "$tail_lines")
[[ "$follow" == true ]] && args+=(-f)

exec "${compose[@]}" "${args[@]}" "${services[@]}"
