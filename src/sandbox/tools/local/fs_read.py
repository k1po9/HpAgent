import os
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from sandbox.tools.types import ToolResult
from ._path_utils import safe_resolve


class FsReadInput(BaseModel):
    path: str = Field(description="File path relative to workspace root")
    offset: int = Field(default=1, description="Start line number (1-indexed)")
    limit: int = Field(default=None, description="Max lines to return")


def create_fs_read_tool(workspace_root: str):
    async def fs_read(path: str, offset: int = 1, limit: int = None) -> str:
        full = safe_resolve(workspace_root, path)
        if not os.path.isfile(full):
            if os.path.isdir(full):
                raise ValueError(
                    f"'{path}' is a directory. Use Glob to list its contents."
                )
            raise ValueError(
                f"File not found: {path}"
            )

        with open(full, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        start = max(offset - 1, 0)
        end = start + limit if limit else total
        selected = lines[start:end]

        out_lines = []
        for i, line in enumerate(selected):
            out_lines.append(f"{start + i + 1}\t{line.rstrip()}")

        result = "\n".join(out_lines)

        if limit is None and total > 2000:
            truncated_msg = (
                f"\n\n--- File has {total} lines. "
                f"Showing first 500. Use offset/limit to read more. ---"
            )
            return result[:result.rfind("\n", 0, len(result))] + truncated_msg

        if end < total:
            result += f"\n\n--- Lines {start + 1}-{min(end, total)} of {total} (truncated) ---"

        return result

    return StructuredTool.from_function(
        name="fs_read",
        description="Read a file with line numbers. Each returned line is prefixed with 'lineno\\t'. Use offset/limit for large files.",
        args_schema=FsReadInput,
        coroutine=fs_read,
    )
