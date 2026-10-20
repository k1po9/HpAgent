"""Offline contracts for maintained documentation and its public entrypoints."""
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("docs_contract", ROOT / "scripts/check/docs-links.py")
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


class DocumentationContractTests(unittest.TestCase):
    def test_discovers_nested_docs_and_agent_instructions_without_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("docs/design/proposal.md", "web/components/AGENTS.md",
                         "scripts/benchmarks/README.md", "prototypes/lab/README.md",
                         "web/node_modules/pkg/README.md", "web/dist/README.md"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("content")
            found = {str(p.relative_to(root)) for p in links.documentation_paths(root)}
            self.assertIn("AGENTS.md", found)
            self.assertIn("web/components/AGENTS.md", found)
            self.assertIn("docs/design/proposal.md", found)
            self.assertIn("scripts/benchmarks/README.md", found)
            self.assertIn("prototypes/lab/README.md", found)
            self.assertNotIn("web/node_modules/pkg/README.md", found)
            self.assertNotIn("web/dist/README.md", found)

    def test_missing_make_and_npm_commands_are_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Makefile").write_text("check docs-check:\n\t@true\n")
            (root / "web").mkdir()
            (root / "web/package.json").write_text('{"scripts":{"build":"vite build"}}')
            path = root / "README.md"
            path.write_text('`make check docs-check`\n`make retired`\n'
                            '```bash\nnpm run build -- --outDir /tmp/check\n'
                            'npm run retired\n```\n```text\nmake ignored\n```\n')
            errors = links.check_commands([path], root)
            self.assertEqual([(e[2], e[3]) for e in errors],
                             [("unknown make target", "retired"),
                              ("unknown npm script", "retired")])

    def test_lab_commands_use_its_own_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lab = root / "prototypes/lab"
            lab.mkdir(parents=True)
            (lab / "package.json").write_text('{"scripts":{"lab-only":"vite"}}')
            path = lab / "README.md"
            path.write_text('`npm run lab-only`')
            self.assertEqual(links.check_commands([path], root), [])

    def test_source_policy_distinguishes_deleted_and_ignored_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            (root / "artifacts").mkdir()
            stale = root / "artifacts/stale.md"
            stale.write_text("old")
            subprocess.run(["git", "add", "artifacts/stale.md"], cwd=root, check=True)
            self.assertEqual(len(links.check_generated_sources(root)), 1)
            stale.unlink()
            (root / ".gitignore").write_text("/artifacts/\n")
            (root / "artifacts/local.md").write_text("ignored local output")
            self.assertEqual(links.check_generated_sources(root), [])
            report = root / "docs/implementation/stage.md"
            report.parent.mkdir(parents=True)
            report.write_text("report")
            self.assertEqual(links.check_generated_sources(root)[0][-1],
                             "docs/implementation/stage.md")

    def test_links_cannot_escape_repository_or_use_ignored_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            (root / ".gitignore").write_text("local.md\n")
            (root / "local.md").write_text("local")
            path = root / "AGENTS.md"
            path.write_text('[escape](../outside.md)\n[local](local.md)\n')
            errors = links.check([path], root)
            self.assertEqual([e[2] for e in errors],
                             ["local link outside repository", "ignored local dependency"])


if __name__ == "__main__":
    unittest.main()
