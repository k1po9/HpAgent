#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo "用法：agent_strategy.sh [check|run]；check可能探测环境，run写隔离实验库并调用模型。详见同目录README。"; exit 0
fi
[[ $# -le 1 && "${1:-check}" =~ ^(check|run)$ ]] || { echo "仅支持 check 或 run" >&2; exit 2; }
cd "$repo_root"
PY="${PYTHON:-python3}"
[[ ! -x .venv/bin/python ]] || PY=.venv/bin/python
exec env PYTHONPATH=src "$PY" scripts/benchmarks/agent_strategy/agent_strategy_experiment.py "${1:-check}"
