import os
import glob as _glob
import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._path_utils import safe_resolve


class GlobInput(BaseModel):
    root: str = Field(description="Directory path relative to workspace root")
    pattern: str = Field(description="Glob pattern, e.g. '**/*.py' or '*.txt'")


def create_glob_tool(workspace_root: str):
    async def glob_(root: str, pattern: str) -> str:
        search_dir = safe_resolve(workspace_root, root)
        if not os.path.isdir(search_dir):
            raise ValueError(
                f"Directory not found: {root}. Check the path or try a different root."
            )

        matches = _glob.glob(pattern, root_dir=search_dir, recursive=True)
        results = []
        for m in matches:
            full = os.path.join(search_dir, m)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            results.append({"path": m, "size": size})

        results.sort(key=lambda r: os.path.getmtime(
            os.path.join(search_dir, r["path"])
        ), reverse=True)

        total = len(results)
        truncated = total > 200
        if truncated:
            results = results[:200]

        out = {"matches": results}
        if truncated:
            out["truncated"] = True
            out["total_count"] = total
        return json.dumps(out)

    return StructuredTool.from_function(
        name="Glob",
        description="Find files matching a pattern in a directory. Returns relative paths sorted by modification time (newest first). Max 200 results.",
        args_schema=GlobInput,
        coroutine=glob_,
    )
