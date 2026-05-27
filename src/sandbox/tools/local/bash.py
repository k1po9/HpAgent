import asyncio
import json
import os
import re

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._path_utils import safe_cwd

_DANGEROUS_RM = re.compile(r'rm\s+(-[a-zA-Z]*[rf]+|.*\/\*|.*\/\s*$)')


class BashInput(BaseModel):
    cmd: str = Field(description="Shell command to execute")
    cwd: str = Field(default="", description="Working directory relative to workspace root")
    timeout: int = Field(default=120, description="Timeout in seconds (max 300)")


def create_bash_tool(workspace_root: str):
    async def bash(cmd: str, cwd: str = "", timeout: int = 120) -> str:
        resolved_cwd = safe_cwd(workspace_root, cwd)

        if _DANGEROUS_RM.search(cmd):
            raise ValueError(
                f"Blocked: potentially destructive rm command. "
                f"Use targeted fs_edit or individual file removal with 'rm <file>'."
            )

        timeout = min(timeout, 300)

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", cmd,
                cwd=resolved_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise ValueError(
                f"Command timed out after {timeout}s. "
                f"Use a longer timeout or simplify the command."
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        limit = 50_000
        stdout_len = len(stdout)
        if stdout_len > limit:
            stdout = stdout[:limit] + (
                f"\n\n--- Output truncated ({limit} bytes shown "
                f"of {stdout_len} total) ---"
            )

        return json.dumps({
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
        })

    return StructuredTool.from_function(
        name="Bash",
        description="Execute a shell command inside the workspace. cwd is relative to workspace root. stdout capped at 50KB. Default timeout 120s. Use for: mkdir, rm, mv, cp, git, npm, pip, etc.",
        args_schema=BashInput,
        coroutine=bash,
    )
