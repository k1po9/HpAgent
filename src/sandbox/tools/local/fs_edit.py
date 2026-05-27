import os
import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._path_utils import safe_resolve


class FsEditInput(BaseModel):
    path: str = Field(description="File path relative to workspace root")
    old_string: str = Field(description="Exact string to replace. Empty string = append to end of file.")
    new_string: str = Field(description="Replacement string")


def create_fs_edit_tool(workspace_root: str):
    async def fs_edit(path: str, old_string: str, new_string: str) -> str:
        full = safe_resolve(workspace_root, path)
        if not os.path.isfile(full):
            raise ValueError(
                f"File not found: {path}. Use fs_write to create new files."
            )

        with open(full, "r", encoding="utf-8") as f:
            content = f.read()

        if old_string == "":
            with open(full, "w", encoding="utf-8") as f:
                f.write(content + new_string)
            return json.dumps({"replacements": 1})

        count = content.count(old_string)
        if count == 0:
            raise ValueError(
                f"old_string not found in {path}. "
                f"The file may have changed. Use fs_read to see current content, "
                f"or use Grep to search for the target text."
            )
        if count > 1:
            lines = content.split("\n")
            match_lines = []
            for i, line in enumerate(lines):
                if old_string in line:
                    match_lines.append(str(i + 1))
            raise ValueError(
                f"old_string matched {count} times in {path} "
                f"(lines {', '.join(match_lines)}). "
                f"Add more surrounding context to make it unique."
            )

        new_content = content.replace(old_string, new_string, 1)
        with open(full, "w", encoding="utf-8") as f:
            f.write(new_content)
        return json.dumps({"replacements": 1})

    return StructuredTool.from_function(
        name="fs_edit",
        description="Replace exactly one occurrence of old_string with new_string in a file. old_string must match exactly once — whitespace matters. Use empty old_string to append to end.",
        args_schema=FsEditInput,
        coroutine=fs_edit,
    )
