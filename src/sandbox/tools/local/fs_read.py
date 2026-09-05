import os

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._path_utils import safe_resolve


class FsReadInput(BaseModel):
    path: str = Field(description="File path relative to workspace root")
    offset: int = Field(default=1, description="Start line number (1-indexed)")
    limit: int | None = Field(default=None, ge=1, le=2000, description="Max lines to return")


def create_fs_read_tool(workspace_root: str):
    async def fs_read(path: str, offset: int = 1, limit: int | None = None) -> str:
        full = safe_resolve(workspace_root, path)
        if not os.path.isfile(full) or os.path.islink(full):
            if os.path.isdir(full):
                raise ValueError(
                    f"'{path}' is a directory. Use Glob to list its contents."
                )
            raise ValueError(
                f"File not found: {path}"
            )

        start = max(offset - 1, 0)
        requested = min(limit or 500, 2000)
        max_return_bytes = 256 * 1024
        out_lines = []
        returned_bytes = 0
        truncated = False
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(full, flags)
        try:
            with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as stream:
                fd = -1
                for index, line in enumerate(stream):
                    if index < start:
                        continue
                    if len(out_lines) >= requested:
                        truncated = True
                        break
                    rendered = f"{index + 1}\t{line.rstrip()}"
                    encoded = rendered.encode("utf-8")
                    if returned_bytes + len(encoded) > max_return_bytes:
                        truncated = True
                        break
                    returned_bytes += len(encoded) + (1 if out_lines else 0)
                    out_lines.append(rendered)
        finally:
            if fd >= 0:
                os.close(fd)

        result = "\n".join(out_lines)
        if truncated:
            result += (
                f"\n\n--- Showing at most {requested} lines / "
                f"{max_return_bytes} UTF-8 bytes from line {start + 1}; use offset/limit to continue. ---"
            )
        return result

    return StructuredTool.from_function(
        name="fs_read",
        description="Read a text file from the persistent Git workspace with line numbers. This tool does not read files uploaded with the current Web Run; use a dedicated Current Run File tool for those files. Each line is prefixed with 'lineno\\t'.",
        args_schema=FsReadInput,
        coroutine=fs_read,
    )
