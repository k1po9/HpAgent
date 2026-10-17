#!/usr/bin/env bash
# Stable compatibility entry; implementation and argument help are stdlib Python.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "${PYTHON:-python3}" "$ROOT/scripts/check/release_smoke.py" "$@"
