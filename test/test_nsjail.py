"""
nsjail sandbox execution tests —— 验证 nsjail 可选加固层 (Bash 专用).

测试覆盖:
  1. NsjailConfig.build_command() 命令行编译
  2. NsjailExecutor.execute() 通过 nsjail 执行 Bash 命令
  3. 错误场景: 超时、nsjail 缺失
"""
import asyncio
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sandbox.nsjail import NsjailConfig, NsjailExecutor


class TestNsjailConfig:
    """Tests for NsjailConfig.build_command() with direct Bash command mode."""

    def test_build_command_basic(self):
        config = NsjailConfig(
            nsjail_binary="/usr/bin/nsjail",
            chroot_path="/sandbox",
            work_dir="/work",
        )
        cmd = config.build_command("echo hello")

        assert cmd[0] == "/usr/bin/nsjail"
        assert "--mode" in cmd
        assert "o" in cmd
        assert "--chroot" in cmd
        assert "/sandbox" in cmd
        assert "--time_limit" in cmd
        assert "--disable_proc" in cmd
        assert "--iface_no_lo" in cmd
        assert "--really_quiet" in cmd

        sep_idx = cmd.index("--")
        exec_cmd = cmd[sep_idx + 1:]
        assert exec_cmd == ["/bin/bash", "-c", "echo hello"]

    def test_build_command_rw_mode(self):
        config = NsjailConfig(readonly_root=False)
        cmd = config.build_command("true")
        assert "--rw" in cmd

    def test_build_command_network_enabled(self):
        config = NsjailConfig(disable_network=False)
        cmd = config.build_command("true")
        assert "--iface_no_lo" not in cmd

    def test_build_command_proc_enabled(self):
        config = NsjailConfig(disable_proc=False)
        cmd = config.build_command("true")
        assert "--disable_proc" not in cmd

    def test_build_command_resource_limits(self):
        config = NsjailConfig(
            time_limit=60,
            memory_limit_mb=512,
            cpu_limit_seconds=20,
            max_processes=16,
            max_files=128,
        )
        cmd = config.build_command("true")

        def get_after(lst, key):
            return lst[lst.index(key) + 1]

        assert get_after(cmd, "--time_limit") == "60"
        assert get_after(cmd, "--rlimit_as") == "512"
        assert get_after(cmd, "--rlimit_cpu") == "20"
        assert get_after(cmd, "--rlimit_nproc") == "16"
        assert get_after(cmd, "--rlimit_nofile") == "128"

    def test_build_command_with_work_dir_override(self):
        config = NsjailConfig(work_dir="/work")
        cmd = config.build_command("ls", work_dir="/custom")
        cwd_idx = cmd.index("--cwd")
        assert cmd[cwd_idx + 1] == "/custom"

    def test_build_command_bind_mounts(self):
        config = NsjailConfig(bind_mounts=["/host/ws:/work"])
        cmd = config.build_command("true")
        assert "--bindmount" in cmd
        mount_idx = cmd.index("--bindmount")
        assert cmd[mount_idx + 1] == "/host/ws:/work"

    def test_build_command_bind_mount_ro(self):
        config = NsjailConfig(bind_mounts=["/host/skills:/skills:ro"])
        cmd = config.build_command("true")
        assert "--bindmount_ro" in cmd


class TestNsjailExecutor:
    """Tests for NsjailExecutor with real nsjail binary."""

    @pytest.fixture
    def executor(self):
        nsjail_bin = "/usr/bin/nsjail"
        if not os.path.isfile(nsjail_bin):
            pytest.skip(f"nsjail binary not found at {nsjail_bin}")

        config = NsjailConfig(
            nsjail_binary=nsjail_bin,
            chroot_path="/",
            work_dir="/tmp",
            time_limit=10,
            memory_limit_mb=512,
            disable_proc=False,
            disable_network=False,
            readonly_root=False,
            really_quiet=True,
        )
        return NsjailExecutor(config)

    @pytest.mark.asyncio
    async def test_bash_echo_via_nsjail(self, executor):
        result = await executor.execute("Bash", {"cmd": "echo hello"})
        assert result.success is True
        output = json.loads(result.output)
        assert "hello" in output["stdout"]
        assert output["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_bash_exit_code(self, executor):
        result = await executor.execute("Bash", {"cmd": "exit 42"})
        output = json.loads(result.output)
        assert output["exit_code"] == 42
        # nsjail returns the exit code, so success depends on exit code
        assert result.success is False  # exit 42 -> non-zero

    @pytest.mark.asyncio
    async def test_execution_id_present(self, executor):
        result = await executor.execute("Bash", {"cmd": "true"})
        assert "execution_id" in result.metadata
        assert "elapsed_ms" in result.metadata


def test_nsjail_config_defaults_are_safe():
    config = NsjailConfig()
    assert config.disable_proc is True
    assert config.disable_network is True
    assert config.readonly_root is True
    assert config.user == "nobody"
    assert config.time_limit > 0
    assert config.memory_limit_mb > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
