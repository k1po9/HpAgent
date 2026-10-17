#!/usr/bin/env python3
"""Check local Markdown links in current documentation; never access network."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ["README.md", "docs/README.md", "scripts/README.md"]
SECTIONS = ["architecture", "development", "operations", "reference"]
HISTORICAL = {"repository-cleanup-audit.md"}
LINK = re.compile(r'!?\[[^\]\n]*\]\((<[^>]+>|[^)\n]+)\)')
REFERENCE = re.compile(r'^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)')


def local_links(text):
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif fence == marker[1][0]:
                fence = None
            continue
        if fence:
            continue
        matches = [m[1] for m in LINK.finditer(line)]
        reference = REFERENCE.match(line)
        if reference:
            matches.append(reference[1])
        for value in matches:
            value = value.split(' "')[0].strip('<>')
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            yield number, unquote(parsed.path)


def check(paths, root=ROOT, check_ignored=True):
    errors = []
    for path in paths:
        for number, target in local_links(path.read_text(encoding="utf-8")):
            candidate = (path.parent / target).resolve()
            if not candidate.is_relative_to(root):
                errors.append((path, number, "local link outside repository", target))
            elif not candidate.exists():
                errors.append((path, number, "missing", target))
            elif check_ignored:
                relative = str(candidate.relative_to(root))
                ignored = subprocess.run(["git", "check-ignore", "-q", "--", relative], cwd=root).returncode
                if ignored == 0:
                    errors.append((path, number, "ignored local dependency", target))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-history", action="store_true", help="同时检查design/implementation历史文档，可能命中既有旧引用")
    args = parser.parse_args()
    paths = [ROOT / name for name in CURRENT]
    for section in SECTIONS:
        paths += [p for p in (ROOT / "docs" / section).rglob("*.md") if p.name not in HISTORICAL]
    # Current contract and historical indexes must also be usable without ignored local archives.
    paths += [ROOT / "docs/design/README.md", ROOT / "docs/implementation/README.md", ROOT / "docs/design/ui-iteration-2026-10/README.md"]
    if args.include_history:
        paths += list((ROOT / "docs/design").rglob("*.md")) + list((ROOT / "docs/implementation").rglob("*.md"))
    paths = sorted(set(paths))
    missing_files = [p for p in paths if not p.is_file()]
    errors = check([p for p in paths if p.is_file()])
    for path in missing_files:
        print(f"missing documentation: {path.relative_to(ROOT)}")
    for path, line, reason, target in errors:
        print(f"{path.relative_to(ROOT)}:{line}: {reason}: {target}")
    print(f"Checked {len(paths)} Markdown files; {len(errors) + len(missing_files)} local link errors. Remote URLs/anchors are not validated.")
    return int(bool(errors or missing_files))


if __name__ == "__main__":
    raise SystemExit(main())
