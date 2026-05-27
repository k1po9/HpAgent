import os
import re
import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._path_utils import safe_resolve


class GrepInput(BaseModel):
    root: str = Field(description="Directory path relative to workspace root")
    query: str = Field(description="Text to search for (literal match by default)")
    glob: str = Field(default=None, description="Optional file pattern filter, e.g. '*.py'")
    regex: bool = Field(default=False, description="Set to True to treat query as a regex pattern")


def create_grep_tool(workspace_root: str):
    async def grep(root: str, query: str, glob: str = None, regex: bool = False) -> str:
        search_dir = safe_resolve(workspace_root, root)
        if not os.path.isdir(search_dir):
            raise ValueError(
                f"Directory not found: {root}"
            )

        if regex:
            try:
                pattern = re.compile(query)
            except re.error as e:
                raise ValueError(f"Invalid regex: {e}")
        else:
            pattern = re.compile(re.escape(query))

        if glob:
            import glob as _glob
            files = _glob.glob(glob, root_dir=search_dir, recursive=True)
        else:
            files = []
            for dirpath, _, filenames in os.walk(search_dir):
                for fn in filenames:
                    rel_dir = os.path.relpath(dirpath, search_dir)
                    if rel_dir == ".":
                        files.append(fn)
                    else:
                        files.append(os.path.join(rel_dir, fn))

        results = []
        for fpath in files:
            full = os.path.join(search_dir, fpath)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f):
                        match = pattern.search(line)
                        if match:
                            results.append({
                                "path": fpath,
                                "line": i + 1,
                                "col": match.start() + 1,
                                "content": line.rstrip(),
                            })
                            if len(results) >= 50:
                                break
                    if len(results) >= 50:
                        break
            except (OSError, UnicodeDecodeError):
                continue

        total = len(results)
        truncated = total >= 50
        if truncated:
            results = results[:50]

        out = {"matches": results}
        if truncated:
            out["truncated"] = True
            out["total_count"] = total
        return json.dumps(out)

    return StructuredTool.from_function(
        name="Grep",
        description="Search file contents for a literal string (or regex). Returns matching lines with file path, line number, and column. Max 50 results. Default is literal match — use regex=True for patterns.",
        args_schema=GrepInput,
        coroutine=grep,
    )
