"""Thin publication adapter from persisted Research Markdown to Web files."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from workspace.file_scope import RunFileScope

from .output import OutputPublisher, PublishedOutput


class ResearchMarkdownPublisher:
    def __init__(self, publisher: OutputPublisher) -> None:
        self.publisher = publisher

    def publish(self, run_id: UUID, markdown: str) -> PublishedOutput:
        operation_id = f"research:{run_id}:markdown-output:v1"
        logical_name = "research-report.md"
        with TemporaryDirectory(prefix="hpagent-research-output-") as root:
            base = Path(root)
            inputs, scratch, outputs = base / "inputs", base / "scratch", base / "outputs"
            for directory in (inputs, scratch, outputs):
                directory.mkdir()
            scope = RunFileScope(run_id, inputs, scratch, outputs, ())
            replayed = self.publisher.replay(scope, operation_id, logical_name)
            if replayed is not None:
                return replayed
            (outputs / logical_name).write_text(markdown, encoding="utf-8")
            return self.publisher.publish(
                scope, operation_id, logical_name, "text/markdown; charset=utf-8",
                encoding="utf-8",
            )
