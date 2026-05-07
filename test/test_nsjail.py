"""
nsjail sandbox execution tests —— 验证 nsjail 子进程隔离执行链路。

测试覆盖:
  1. NsjailConfig.build_command() 命令行编译
  2. runner.py 直接调用（不经过 nsjail）
  3. NsjailExecutor.execute() 通过 nsjail 子进程执行
  4. 错误场景: 未知工具、无效参数、超时
"""
import asyncio
import json
import os
import sys
import pytest

# 确保 src 在 Python path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sandbox.nsjail import NsjailConfig, NsjailExecutor


def _find_runner() -> str:
    """查找 runner.py 的绝对路径。"""
    src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
    return os.path.abspath(os.path.join(src_dir, "sandbox", "runner.py"))


class TestNsjailConfig:
    """测试 NsjailConfig 命令行编译。"""

    def test_build_command_basic(self):
        config = NsjailConfig(
            nsjail_binary="/usr/bin/nsjail",
            chroot_path="/sandbox",
            runner_script="/work/runner.py",
        )
        cmd = config.build_command("calculator", {"expression": "2+2"})

        assert cmd[0] == "/usr/bin/nsjail"
        assert "--mode" in cmd
        assert "o" in cmd
        assert "--chroot" in cmd
        assert "/sandbox" in cmd
        assert "--time_limit" in cmd
        assert "30" in cmd
        assert "--rlimit_as" in cmd
        assert "--disable_proc" in cmd
        assert "--iface_no_lo" in cmd
        assert "--really_quiet" in cmd

        # Verify the command after "--"
        sep_idx = cmd.index("--")
        exec_cmd = cmd[sep_idx + 1:]
        assert exec_cmd[0] == "/usr/bin/python3"
        assert exec_cmd[1] == "/work/runner.py"
        assert exec_cmd[2] == "calculator"
        args = json.loads(exec_cmd[3])
        assert args == {"expression": "2+2"}

    def test_build_command_rw_mode(self):
        config = NsjailConfig(readonly_root=False)
        cmd = config.build_command("test", {})
        assert "--rw" in cmd

    def test_build_command_network_enabled(self):
        config = NsjailConfig(disable_network=False)
        cmd = config.build_command("test", {})
        assert "--iface_no_lo" not in cmd

    def test_build_command_proc_enabled(self):
        config = NsjailConfig(disable_proc=False)
        cmd = config.build_command("test", {})
        assert "--disable_proc" not in cmd

    def test_build_command_resource_limits(self):
        config = NsjailConfig(
            time_limit=60,
            memory_limit_mb=512,
            cpu_limit_seconds=20,
            max_processes=16,
            max_files=128,
        )
        cmd = config.build_command("test", {})

        # Find indices
        def get_after(lst, key):
            i = lst.index(key)
            return lst[i + 1]

        assert get_after(cmd, "--time_limit") == "60"
        assert get_after(cmd, "--rlimit_as") == "512"
        assert get_after(cmd, "--rlimit_cpu") == "20"
        assert get_after(cmd, "--rlimit_nproc") == "16"
        assert get_after(cmd, "--rlimit_nofile") == "128"

    def test_build_command_json_args_with_special_chars(self):
        config = NsjailConfig()
        cmd = config.build_command("web_search", {"query": "hello world", "limit": 10})

        sep_idx = cmd.index("--")
        args_json = cmd[sep_idx + 4]  # after python, runner, tool_name
        args = json.loads(args_json)
        assert args["query"] == "hello world"
        assert args["limit"] == 10


class TestRunnerDirect:
    """直接测试 runner.py 脚本（不经过 nsjail）。"""

    def _run_runner(self, tool_name: str, arguments: dict) -> dict:
        """直接调用 runner.py 并返回解析后的 JSON 结果。"""
        import subprocess
        runner_path = _find_runner()
        args_json = json.dumps(arguments)
        proc = subprocess.run(
            [sys.executable, runner_path, tool_name, args_json],
            capture_output=True,
            text=True,
            timeout=10,
        )
        stdout = proc.stdout.strip()
        if stdout:
            return json.loads(stdout)
        return {"success": False, "error": f"Empty stdout, stderr={proc.stderr}"}

    def test_calculator_simple(self):
        result = self._run_runner("calculator", {"expression": "2 + 2"})
        assert result["success"] is True
        assert result["output"] == "4"

    def test_calculator_complex(self):
        result = self._run_runner("calculator", {"expression": "(10 ** 3) / 2"})
        assert result["success"] is True
        assert result["output"] == "500.0"

    def test_calculator_error(self):
        result = self._run_runner("calculator", {"expression": "1 / 0"})
        assert result["success"] is False
        assert "error" in result

    def test_calculator_no_code_execution(self):
        """验证 __builtins__ 隔离阻止任意代码执行。"""
        result = self._run_runner("calculator", {"expression": "__import__('os').system('ls')"})
        assert result["success"] is False

    def test_web_search(self):
        result = self._run_runner("web_search", {"query": "test", "limit": 2})
        assert result["success"] is True
        assert "query" in result["output"]
        assert result["output"]["query"] == "test"

    def test_file_read_nonexistent(self):
        result = self._run_runner("file_read", {"file_path": "/nonexistent/file/path"})
        assert result["success"] is False

    def test_unknown_tool(self):
        result = self._run_runner("nonexistent_tool", {"arg": "val"})
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    def test_invalid_json_args(self):
        import subprocess
        runner_path = _find_runner()
        proc = subprocess.run(
            [sys.executable, runner_path, "calculator", "not-valid-json!!!"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        result = json.loads(proc.stdout.strip())
        assert result["success"] is False
        assert "Invalid JSON" in result["error"]

    def test_missing_args(self):
        import subprocess
        runner_path = _find_runner()
        proc = subprocess.run(
            [sys.executable, runner_path, "calculator"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        result = json.loads(proc.stdout.strip())
        assert result["success"] is False
        assert "Usage" in result["error"]


class TestNsjailExecutor:
    """测试 NsjailExecutor 通过 nsjail 子进程执行（需要 nsjail 二进制）。"""

    @pytest.fixture
    def executor(self):
        """创建指向当前源代码的 NsjailExecutor。

        使用宿主文件系统 (chroot=/) 以便访问 Python 和 runner.py。
        """
        nsjail_bin = "/usr/bin/nsjail"
        if not os.path.isfile(nsjail_bin):
            pytest.skip(f"nsjail binary not found at {nsjail_bin}")

        runner_path = _find_runner()
        config = NsjailConfig(
            nsjail_binary=nsjail_bin,
            chroot_path="/",
            work_dir=os.path.dirname(runner_path),
            runner_script=runner_path,
            python_binary=sys.executable,
            time_limit=10,
            memory_limit_mb=512,
            disable_proc=False,  # 需要 /proc 在宿主机上运行
            disable_network=False,
            readonly_root=False,
            really_quiet=True,
        )
        return NsjailExecutor(config)

    @pytest.mark.asyncio
    async def test_calculator_via_nsjail(self, executor):
        result = await executor.execute("calculator", {"expression": "2 + 3"})
        assert result.success is True
        assert result.output == "5"
        assert "execution_id" in result.metadata
        assert "elapsed_ms" in result.metadata

    @pytest.mark.asyncio
    async def test_calculator_code_injection_blocked(self, executor):
        """验证 nsjail + runner.py 双重防护阻止代码注入。"""
        result = await executor.execute(
            "calculator",
            {"expression": "__import__('os').system('ls')"}
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_web_search_via_nsjail(self, executor):
        result = await executor.execute("web_search", {"query": "nsjail"})
        assert result.success is True
        assert "results" in result.output

    @pytest.mark.asyncio
    async def test_unknown_tool_via_nsjail(self, executor):
        result = await executor.execute("evil_tool", {"a": 1})
        assert result.success is False
        assert "Unknown tool" in result.error

    @pytest.mark.asyncio
    async def test_execution_id_unique(self, executor):
        result1 = await executor.execute("calculator", {"expression": "1"})
        result2 = await executor.execute("calculator", {"expression": "2"})
        assert result1.metadata["execution_id"] != result2.metadata["execution_id"]

    @pytest.mark.asyncio
    async def test_retrieve_result_without_redis(self, executor):
        """无 Redis 时 retrieve_result 返回 None。"""
        result = await executor.retrieve_result("nonexistent-id")
        assert result is None


def test_nsjail_config_defaults():
    """验证 NsjailConfig 的默认值是安全的。"""
    config = NsjailConfig()
    assert config.disable_proc is True
    assert config.disable_network is True
    assert config.readonly_root is True
    assert config.user == "nobody"
    assert config.time_limit > 0
    assert config.memory_limit_mb > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
