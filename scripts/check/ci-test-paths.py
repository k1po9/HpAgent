#!/usr/bin/env python3
"""Fail early when CI explicitly names a test file that does not exist."""
import re
import sys
from pathlib import Path


def missing_test_paths(workflow: Path, root: Path) -> list[str]:
    paths = set(re.findall(r"\btest/[\w./-]+\.py\b", workflow.read_text()))
    return sorted(path for path in paths if not (root / path).is_file())


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    missing = missing_test_paths(root / ".github/workflows/ci.yml", root)
    if missing:
        print("CI references missing tests:\n" + "\n".join(missing), file=sys.stderr)
        return 1
    print("All explicit CI test paths exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
