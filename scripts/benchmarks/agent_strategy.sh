#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

if [[ ! -x .venv/bin/python ]]; then
  echo "missing .venv/bin/python; create the project virtualenv first" >&2
  exit 2
fi

exec env PYTHONPATH=src .venv/bin/python \
  scripts/benchmarks/agent_strategy_experiment.py "${1:-check}"
