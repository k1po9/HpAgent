#!/usr/bin/env python3
"""Validate current Markdown links and documented make/npm run commands offline."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ["README.md", "AGENTS.md", "web/AGENTS.md", "docs/README.md", "scripts/README.md"]
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


def documentation_paths(root=ROOT):
    """Discover all maintained docs, including nested agent instructions and labs."""
    paths = {root / name for name in CURRENT}
    paths.update((root / "docs").rglob("*.md"))
    paths.update((root / "src").rglob("AGENTS.md"))
    for directory in ("web", "scripts", "prototypes"):
        base = root / directory
        if base.exists():
            # Prune dependency/output trees rather than traversing node_modules.
            pending = [base]
            while pending:
                parent = pending.pop()
                for child in parent.iterdir():
                    if child.is_dir() and child.name not in {
                        "node_modules", "dist", "test-results", "playwright-report", ".git"
                    }:
                        pending.append(child)
                    elif child.is_file() and child.name in {"README.md", "AGENTS.md"}:
                        paths.add(child)
    return sorted(paths)


def documented_commands(text):
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s*(`{3,}|~{3,})(\w*)', line)
        if marker:
            fence = None if fence else marker[2] or "text"
            continue
        if fence:
            chunks = [line.strip()] if fence in {"bash", "sh", "shell"} else []
        else:
            chunks = re.findall(r'(?<!`)`([^`\n]+)`(?!`)', line)
        for chunk in chunks:
            # Public command references must begin with the command, not prose/examples of output.
            if re.match(r'^(make\s+|npm\s+run\s+)', chunk):
                yield number, chunk


def check_commands(paths, root=ROOT):
    errors = []
    makefile = root / "Makefile"
    targets = set()
    if makefile.exists():
        for match in re.finditer(r'^([\w .-]+):(?!=)', makefile.read_text(), re.M):
            targets.update(match[1].split())
    for path in paths:
        package = next((p / "package.json" for p in [path.parent, *path.parents]
                        if p.is_relative_to(root) and (p / "package.json").is_file()),
                       root / "web/package.json")
        scripts = json.loads(package.read_text()).get("scripts", {}) if package.exists() else {}
        for number, command in documented_commands(path.read_text(encoding="utf-8")):
            try:
                words = shlex.split(command, comments=True)
            except ValueError:
                errors.append((path, number, "invalid command quoting", command))
                continue
            if words[0] == "npm":
                if len(words) > 2 and words[2] not in scripts:
                    errors.append((path, number, "unknown npm script", words[2]))
                continue
            for word in words[1:]:
                if word in {"&&", ";", "|", "||"}:
                    break
                if word.startswith("-") or "=" in word:
                    continue
                if word not in targets:
                    errors.append((path, number, "unknown make target", word))
    return errors


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


def check_generated_sources(root=ROOT):
    """Use Git's source set; ignored local outputs and unstaged deletions are excluded."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return [(root / name, 1, "generated/history material in source tree", name)
            for name in result.stdout.split("\0")
            if name.startswith(("artifacts/", "docs/implementation/")) and (root / name).is_file()]


def main():
    paths = documentation_paths()
    missing = [p for p in paths if not p.is_file()]
    present = [p for p in paths if p.is_file()]
    errors = check(present) + check_commands(present) + check_generated_sources()
    for path in missing:
        print(f"missing documentation: {path.relative_to(ROOT)}")
    for path, line, reason, target in errors:
        print(f"{path.relative_to(ROOT)}:{line}: {reason}: {target}")
    print(f"Checked {len(paths)} Markdown files; {len(errors) + len(missing)} errors. "
          "Remote URLs and anchors are not validated; commands are not executed.")
    return int(bool(errors or missing))


if __name__ == "__main__":
    raise SystemExit(main())
