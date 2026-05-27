"""
Nsjail sandbox executor —— 可选的 OS 级隔离层，仅对 Bash 工具加固。

默认关闭。启用后，Bash 工具的命令通过 nsjail 子进程执行:
  nsjail --mode o ... -- /bin/bash -c '<command>'

其他本地工具（fs_read 等）始终进程内执行，不走 nsjail。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sandbox.tools.types import ToolResult

logger = logging.getLogger("HpAgent.Nsjail")


@dataclass
class NsjailConfig:
    """nsjail 隔离配置 —— 仅用于 Bash 命令加固。"""

    nsjail_binary: str = "/usr/bin/nsjail"
    chroot_path: str = "/"
    work_dir: str = "/work"
    readonly_root: bool = True

    user: str = "nobody"
    group: str = "nogroup"
    hostname: str = "sandbox"

    time_limit: int = 30
    memory_limit_mb: int = 256
    cpu_limit_seconds: int = 10
    max_processes: int = 32
    max_files: int = 64

    disable_proc: bool = True
    disable_network: bool = True

    bind_mounts: list[str] = field(default_factory=list)
    really_quiet: bool = True

    def build_command(self, bash_cmd: str, *, work_dir: Optional[str] = None) -> list[str]:
        cmd = [
            self.nsjail_binary,
            "--mode", "o",
            "--chroot", self.chroot_path,
            "--hostname", self.hostname,
            "--cwd", work_dir or self.work_dir,
            "--user", self.user,
            "--group", self.group,
            "--time_limit", str(self.time_limit),
            "--rlimit_as", str(self.memory_limit_mb),
            "--rlimit_cpu", str(self.cpu_limit_seconds),
            "--rlimit_nofile", str(self.max_files),
            "--rlimit_nproc", str(self.max_processes),
        ]

        if self.disable_proc:
            cmd.append("--disable_proc")
        if self.disable_network:
            cmd.append("--iface_no_lo")
        if not self.readonly_root:
            cmd.append("--rw")
        if self.really_quiet:
            cmd.append("--really_quiet")

        for mount_spec in self.bind_mounts:
            if mount_spec.endswith(":ro"):
                inner = mount_spec[:-3]
                cmd += ["--bindmount_ro", inner]
            elif mount_spec.endswith(":rw"):
                inner = mount_spec[:-3]
                cmd += ["--bindmount", inner]
            else:
                cmd += ["--bindmount", mount_spec]

        cmd += ["--", "/bin/bash", "-c", bash_cmd]
        return cmd


class NsjailExecutor:
    """仅对 Bash 工具提供 nsjail 加固的执行器。"""

    def __init__(self, config: NsjailConfig, redis_cache: Any = None):
        self.config = config
        self._redis_cache = redis_cache
        if not os.path.isfile(self.config.nsjail_binary):
            logger.warning(
                "DEGRADATION: nsjail binary not found at %s → Bash will run in-process",
                self.config.nsjail_binary,
            )

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        execution_id = str(uuid.uuid4())
        start_time = time.time()

        bash_cmd = arguments.get("cmd", "")
        work_dir = self.config.work_dir

        cmd = self.config.build_command(bash_cmd, work_dir=work_dir)
        logger.debug("nsjail exec [%s]: %s", execution_id, " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.time_limit + 5,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error=f"Command timed out after {self.config.time_limit}s",
                metadata={"execution_id": execution_id},
            )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error=f"nsjail binary not found: {self.config.nsjail_binary}",
                metadata={"execution_id": execution_id},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Subprocess error: {str(e)}",
                metadata={"execution_id": execution_id},
            )

        elapsed_ms = (time.time() - start_time) * 1000
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        return ToolResult(
            success=proc.returncode == 0,
            output=json.dumps({
                "stdout": stdout[:50000],
                "stderr": stderr,
                "exit_code": proc.returncode,
            }),
            metadata={
                "execution_id": execution_id,
                "exit_code": proc.returncode,
                "elapsed_ms": round(elapsed_ms, 2),
            },
        )
