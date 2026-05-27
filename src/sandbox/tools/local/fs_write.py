import os
import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._path_utils import safe_resolve


class FsWriteInput(BaseModel):
    path: str = Field(description="File path relative to workspace root")
    content: str = Field(description="Full file content to write")


def create_fs_write_tool(workspace_root: str):
    async def fs_write(path: str, content: str) -> str:
        full = safe_resolve(workspace_root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            written = f.write(content)
        return json.dumps({"bytes_written": written})

    return StructuredTool.from_function(
        name="fs_write",
        description="Create or completely overwrite a file. Creates intermediate directories automatically. Use fs_edit for targeted changes.",
        args_schema=FsWriteInput,
        coroutine=fs_write,
    )
