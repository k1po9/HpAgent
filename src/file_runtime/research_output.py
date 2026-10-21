"""Thin publication adapter from persisted Research Markdown to Web files."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from research_domain.context import research_identity
from workspace.file_scope import RunFileScope

from .output import OutputPublisher, PublishedOutput


class ResearchMarkdownPublisher:
    def __init__(self, publisher: OutputPublisher) -> None:
        self.publisher = publisher

    def publish(self, run_id: UUID, markdown: str, *, kind: str = "markdown") -> PublishedOutput:
        operation_id = f"research:{research_identity(run_id)}:{kind}-output:v1"
        logical_name = f"research-{research_identity(run_id)}.md" if kind == "markdown" else f"research-{research_identity(run_id)}-checks.json"
        with TemporaryDirectory(prefix="hpagent-research-output-") as root:
            base = Path(root)
            inputs, scratch, outputs = base / "inputs", base / "scratch", base / "outputs"
            for directory in (inputs, scratch, outputs):
                directory.mkdir()
            scope = RunFileScope(run_id, inputs, scratch, outputs, ())
            (outputs / logical_name).write_text(markdown, encoding="utf-8")
            return self.publisher.publish(
                scope, operation_id, logical_name, ("text/markdown; charset=utf-8" if kind == "markdown" else "application/json"),
                encoding="utf-8",
            )
