#!/usr/bin/env python3
"""Check explicit CI paths and require the four shared backend test suites."""
import re
import sys
from pathlib import Path

REQUIRED_SUITES = {"test-unit", "test-db", "test-api", "test-temporal"}


def missing_test_paths(workflow: Path, root: Path) -> list[str]:
    paths = set(re.findall(r"\btest/[\w./-]+\.py\b", workflow.read_text()))
    return sorted(path for path in paths if not (root / path).is_file())


def missing_test_suites(workflow: Path) -> list[str]:
    # Each suite has its own CI service environment. Keep these explicit run
    # steps so a job cannot silently replace a suite with a handful of files.
    targets = set(re.findall(r"^\s*(?:-\s+)?run:\s*make\s+([\w-]+)\b", workflow.read_text(), re.MULTILINE))
    return sorted(REQUIRED_SUITES - targets)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github/workflows/ci.yml"
    missing = missing_test_paths(workflow, root)
    suites = missing_test_suites(workflow)
    if missing:
        print("CI references missing tests:\n" + "\n".join(missing), file=sys.stderr)
    if suites:
        print("CI is missing shared test suites:\n" + "\n".join(suites), file=sys.stderr)
    if missing or suites:
        return 1
    print("All explicit CI test paths exist; unit, PostgreSQL, API and Temporal suites are selected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
