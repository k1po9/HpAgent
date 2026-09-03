"""Resolve logical Run inputs without accepting arbitrary host paths."""

from __future__ import annotations

import mimetypes
import os
import stat
from pathlib import Path
from uuid import UUID

from file_domain.models import FileResource
from workspace.file_scope import RunFileScope


class FileResourceResolver:
    def __init__(self, scope: RunFileScope) -> None:
        self.scope = scope

    def resolve(self, reference: UUID | str) -> FileResource:
        try:
            file_id = reference if isinstance(reference, UUID) else UUID(reference)
        except ValueError:
            file_id = None
        candidates = [
            candidate for candidate in (*self.scope.inputs, *self.scope.outputs)
            if candidate.file_id == file_id or candidate.logical_name == reference
        ]
        if not candidates:
            raise LookupError("file is not an input or published output of the active Run")
        if len(candidates) > 1:
            raise LookupError("logical file name is ambiguous; use file_id")
        item = candidates[0]
        root = self.scope.inputs_root if item.direction == "input" else self.scope.outputs_root
        path = (root / item.logical_name).absolute()
        if not path.is_relative_to(root) or path.is_symlink():
            raise ValueError("file escapes Run file scope")
        mode = os.stat(path, follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("Run input is not a regular file")
        media_type = item.content_type or mimetypes.guess_type(Path(item.logical_name).name)[0]
        return FileResource(
            file_id=item.file_id,
            logical_name=item.logical_name,
            local_path=path,
            size_bytes=item.size_bytes,
            media_type=media_type or "application/octet-stream",
            encoding=item.encoding,
            sha256=item.sha256,
        )
