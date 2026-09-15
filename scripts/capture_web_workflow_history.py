"""Capture W1-C canonical lifecycle histories from isolated PG/Temporal E2E.

Usage: .venv/bin/python scripts/capture_web_workflow_history.py --output-dir /tmp/histories
The runner creates disposable fixtures and captures new histories, never rewrites
legacy histories as a compatibility claim. Final histories are replayed by E2E.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for strategy in ("react", "plan_and_execute"):
        if (args.output_dir / f"lifecycle_{strategy}_completed.json").exists():
            parser.error("choose an output directory without existing lifecycle histories")
    return subprocess.run([
        sys.executable, str(root / "scripts/verify_w1c_contracts.py"),
        "test/web_persistence/test_web_temporal_e2e.py", "-k", "completed",
    ], cwd=root, env=dict(os.environ, W1C_HISTORY_DIR=str(args.output_dir.resolve()))).returncode


if __name__ == "__main__":
    raise SystemExit(main())
