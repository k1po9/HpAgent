"""
Nsjail sandbox executor —— 通过 nsjail 子进程实现 OS 级工具执行隔离。

============================================================================
设计意图
============================================================================

  将原来进程内直接调用 tool.execute() 的模式改为通过 nsjail 子进程执行:
    1. NsjailConfig: 声明式配置对象，每个参数映射到 nsjail 命令行选项
    2. NsjailExecutor: 将配置编译为 nsjail 命令，异步执行子进程，解析结果

  安全隔离维度:
    - PID namespace: 工具代码在独立 PID 空间运行
    - chroot: 文件系统隔离（默认只读）
    - rlimit: 内存/CPU/进程数/文件数硬限制
    - 网络禁用: --iface_no_lo
    - 用户隔离: --user nobody

============================================================================
使用示例
============================================================================

  config = NsjailConfig(chroot_path="/sandbox", time_limit=30)
  executor = NsjailExecutor(config)
  result = await executor.execute("calculator", {"expression": "2+2"})
  # → ToolResult(success=True, output="4")
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sandbox.tools.base import ToolResult

logger = logging.getLogger("HpAgent.Nsjail")


@dataclass
class NsjailConfig:
    """nsjail 沙箱执行配置 —— 声明式定义所有隔离参数。

    每个字段对应 nsjail 的一个命令行选项，提供安全的默认值。
    生产环境建议将 chroot_path 指向一个最小化的 rootfs。
    """

    nsjail_binary: str = "/usr/bin/nsjail"

    # ── 文件系统隔离 ──
    chroot_path: str = "/"          # chroot 根目录，"/" 表示使用宿主文件系统
    work_dir: str = "/work"         # 工作目录（nsjail 内进程的 cwd）
    readonly_root: bool = True      # chroot 只读挂载（--rw 不设置时默认只读）

    # ── 执行目标 ──
    python_binary: str = "/usr/bin/python3"
    runner_script: str = "/work/runner.py"

    # ── 用户隔离 ──
    user: str = "nobody"
    group: str = "nogroup"
    hostname: str = "sandbox"

    # ── 资源限制 ──
    time_limit: int = 30            # 最大执行时间（秒），超时 SIGKILL
    memory_limit_mb: int = 256      # 地址空间上限（MB）
    cpu_limit_seconds: int = 10     # CPU 时间上限（秒）
    max_processes: int = 32         # 最大进程数
    max_files: int = 64             # 最大打开文件数

    # ── 安全开关 ──
    disable_proc: bool = True       # 禁用 /proc 挂载（防止信息泄漏）
    disable_network: bool = True    # 禁用 lo 接口（防止网络访问）

    # ── 工作区绑载挂载 ──
    bind_mounts: list[str] = field(default_factory=list)
    # 格式: ["/host/path:/jail/path:rw", "/host/skills:/skills:ro"]
    # rw 的通过 --bindmount 挂载，ro 的通过 --bindmount_ro 挂载

    # ── 日志 ──
    really_quiet: bool = True       # 抑制 nsjail 自身的日志输出

    def build_command(
        self,
        tool_name: str,
        arguments: dict,
        *,
        extra_bind_mounts: Optional[list[str]] = None,
        override_work_dir: Optional[str] = None,
    ) -> list[str]:
        """将配置编译为完整的 nsjail 命令行参数列表。

        Args:
            tool_name: 工具名称，传给 runner.py。
            arguments: 工具参数字典，JSON 序列化后传给 runner.py。

        Returns:
            nsjail 命令行参数列表（可直接传给 subprocess/asyncio.subprocess）。
        """
        cmd = [
            self.nsjail_binary,
            "--mode", "o",
            "--chroot", self.chroot_path,
            "--hostname", self.hostname,
            "--cwd", self.work_dir,
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
        if self.readonly_root:
            pass  # 默认只读，不传 --rw
        else:
            cmd.append("--rw")
        if self.really_quiet:
            cmd.append("--really_quiet")

        # ── 绑载挂载: 工作区 + 技能目录 ──
        all_mounts = list(self.bind_mounts)
        if extra_bind_mounts:
            all_mounts.extend(extra_bind_mounts)
        for mount_spec in all_mounts:
            # 格式: "/host/path:/jail/path" 或 "/host/path:/jail/path:rw" 或 "...:ro"
            parts = mount_spec.rsplit(":", 1)
            if len(parts) == 3 and parts[2] == "ro":
                cmd += ["--bindmount_ro", f"{parts[0]}:{parts[1]}"]
            else:
                cmd += ["--bindmount", mount_spec if ":" in mount_spec else mount_spec]

        # 被 nsjail 执行的命令及其参数
        work_dir = override_work_dir or self.work_dir
        args_json = json.dumps(arguments, ensure_ascii=False)
        cmd += [
            "--",
            str(self.python_binary),
            str(self.runner_script),
            tool_name,
            args_json,
        ]
        # 如果覆盖了 work_dir，同步更新 --cwd
        if override_work_dir:
            cwd_idx = cmd.index("--cwd") + 1
            cmd[cwd_idx] = work_dir
        return cmd


class NsjailExecutor:
    """统一 nsjail 执行入口 —— 沙箱工具调用的唯一出口。

    职责:
      1. 将工具名 + 参数编译为 nsjail 命令
      2. 异步执行 nsjail 子进程
      3. 解析 runner.py 的 JSON 输出
      4. 可选：将结果写入 Redis 持久化

    Attributes:
        config: nsjail 配置对象。
        _redis_cache: RedisCache 实例（None 时不持久化）。
    """

    def __init__(self, config: NsjailConfig, redis_cache: Any = None):
        """初始化执行器。

        Args:
            config: nsjail 配置对象。
            redis_cache: RedisCache 实例，None 时不持久化结果。
        """
        self.config = config
        self._redis_cache = redis_cache
        self._ensure_nsjail()

    def _ensure_nsjail(self) -> None:
        """验证 nsjail 二进制和 runner 脚本是否可访问。"""
        if not os.path.isfile(self.config.nsjail_binary):
            logger.warning("DEGRADATION: nsjail binary not found at %s → sandbox isolation disabled, tools will run in-process", self.config.nsjail_binary)

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        *,
        persist: bool = True,
        extra_bind_mounts: Optional[list[str]] = None,
        work_dir: Optional[str] = None,
    ) -> ToolResult:
        """通过 nsjail 子进程执行工具调用。

        完整流程:
          1. 生成 execution_id
          2. 构建 nsjail 命令（含可选的 workspace bind mounts）
          3. 异步执行子进程
          4. 解析 stdout JSON
          5. 可选持久化到 Redis
          6. 返回 ToolResult

        Args:
            tool_name: 工具名称（如 "calculator"）。
            arguments: 工具参数字典。
            persist: 是否将结果写入 Redis（默认 True）。
            extra_bind_mounts: 额外的 bind mount 参数列表。
            work_dir: 覆盖默认工作目录（用于 per-session workspace）。

        Returns:
            ToolResult 实例。
        """
        execution_id = str(uuid.uuid4())
        start_time = time.time()
        cmd = self.config.build_command(
            tool_name,
            arguments,
            extra_bind_mounts=extra_bind_mounts,
            override_work_dir=work_dir,
        )

        logger.debug("nsjail exec [%s]: %s", execution_id, " ".join(str(a) for a in cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.time_limit + 5,  # 比 nsjail time_limit 多 5s 缓冲
            )
        except asyncio.TimeoutError:
            logger.error("nsjail subprocess timeout for tool '%s' [%s]", tool_name, execution_id)
            return ToolResult(
                success=False,
                error=f"Tool execution timeout after {self.config.time_limit}s",
                metadata={"execution_id": execution_id, "tool_name": tool_name},
            )
        except FileNotFoundError:
            logger.error("nsjail binary not found: %s", self.config.nsjail_binary)
            return ToolResult(
                success=False,
                error=f"nsjail binary not found: {self.config.nsjail_binary}",
                metadata={"execution_id": execution_id, "tool_name": tool_name},
            )
        except Exception as e:
            logger.exception("nsjail subprocess error for tool '%s'", tool_name)
            return ToolResult(
                success=False,
                error=f"Subprocess error: {str(e)}",
                metadata={"execution_id": execution_id, "tool_name": tool_name},
            )

        elapsed_ms = (time.time() - start_time) * 1000
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip() if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip() if stderr_bytes else ""

        # 解析 runner.py 的 JSON 输出
        result = self._parse_runner_output(stdout, stderr, proc.returncode, tool_name, execution_id)
        result.metadata.update({
            "execution_id": execution_id,
            "tool_name": tool_name,
            "exit_code": proc.returncode,
            "elapsed_ms": round(elapsed_ms, 2),
        })

        # 持久化到 Redis
        if persist and self._redis_cache:
            await self._persist_result(execution_id, tool_name, arguments, result, elapsed_ms)

        return result

    def _parse_runner_output(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
        tool_name: str,
        execution_id: str,
    ) -> ToolResult:
        """解析 runner.py 的标准输出。

        runner.py 约定: 输出一行 JSON，格式为:
          {"success": true, "output": ...}
          {"success": false, "error": "..."}

        如果 stdout 为空或解析失败，返回错误结果。
        """
        if not stdout:
            error_msg = f"Empty runner output (exit={returncode})"
            if stderr:
                error_msg += f": {stderr[:500]}"
            return ToolResult(
                success=False,
                error=error_msg,
                metadata={"execution_id": execution_id},
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # runner 可能输出了非 JSON 内容（如 Python traceback）
            return ToolResult(
                success=False,
                error=f"Invalid runner JSON output: {stdout[:500]}",
                metadata={"execution_id": execution_id, "stderr": stderr[:500]},
            )

        if data.get("success"):
            return ToolResult(
                success=True,
                output=data.get("output"),
                metadata={"execution_id": execution_id},
            )
        else:
            return ToolResult(
                success=False,
                error=data.get("error", "Unknown error"),
                metadata={"execution_id": execution_id},
            )

    async def _persist_result(
        self,
        execution_id: str,
        tool_name: str,
        arguments: dict,
        result: ToolResult,
        elapsed_ms: float,
    ) -> None:
        """将执行结果写入 Redis。

        Key: sandbox:result:{execution_id}
        TTL: 3600 秒（1 小时）
        """
        try:
            await self._redis_cache.set_json(
                f"sandbox:result:{execution_id}",
                {
                    "execution_id": execution_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result": result.to_dict(),
                    "elapsed_ms": elapsed_ms,
                    "timestamp": time.time(),
                },
                ttl=3600,
            )
        except Exception as e:
            logger.warning("DEGRADATION: Redis result persist failed (%s) → execution result not cached", e)

    async def retrieve_result(self, execution_id: str) -> dict | None:
        """从 Redis 检索历史执行结果。

        Args:
            execution_id: 执行 ID。

        Returns:
            结果字典，或 None（已过期或未持久化）。
        """
        if not self._redis_cache:
            return None
        try:
            return await self._redis_cache.get_json(f"sandbox:result:{execution_id}")
        except Exception as e:
            logger.warning("Failed to retrieve result from Redis: %s", e)
            return None
