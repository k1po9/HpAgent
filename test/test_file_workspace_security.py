from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.sandbox_manager import SandboxManager
from sandbox.tools.local._path_utils import safe_cwd, safe_resolve
from sandbox.tools.local.fs_read import create_fs_read_tool
from sandbox.tools.local.glob_ import create_glob_tool
from sandbox.tools.local.grep import create_grep_tool


def test_safe_resolve_rejects_absolute_parent_and_same_prefix(tmp_path: Path) -> None:
    root = tmp_path / "work"
    sibling = tmp_path / "work-evil"
    root.mkdir()
    sibling.mkdir()

    assert safe_resolve(str(root), "nested.txt") == str(root / "nested.txt")
    for attack in (str(sibling / "secret"), "../work-evil/secret", "a/../../secret"):
        with pytest.raises(ValueError):
            safe_resolve(str(root), attack)


def test_safe_resolve_rejects_symlinks_and_safe_cwd_requires_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "work"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "file").write_text("x")

    with pytest.raises(ValueError, match="Symbolic links"):
        safe_resolve(str(root), "escape/secret")
    with pytest.raises(ValueError, match="not a directory"):
        safe_cwd(str(root), "file")


async def test_fs_read_streams_with_bounded_default_output(tmp_path: Path) -> None:
    target = tmp_path / "large.log"
    target.write_text("".join(f"line-{index}\n" for index in range(5000)))
    tool = create_fs_read_tool(str(tmp_path))

    result = await tool.ainvoke({"path": "large.log"})

    assert "1\tline-0" in result
    assert "500\tline-499" in result
    assert "501\tline-500" not in result
    assert "Showing at most 500 lines" in result
    assert len(result.encode("utf-8")) < 256 * 1024 + 512


def test_host_bash_is_an_explicit_default_closed_capability() -> None:
    manager = SandboxManager(native_tools_enabled=True)
    assert manager._host_bash_enabled is False


def test_workspace_tool_descriptions_exclude_current_run_uploads(tmp_path: Path) -> None:
    tools = [
        create_fs_read_tool(str(tmp_path)),
        create_glob_tool(str(tmp_path)),
        create_grep_tool(str(tmp_path)),
    ]
    for tool in tools:
        description = tool.description.lower()
        assert "persistent git workspace" in description
        assert "current web run" in description


def test_run_file_scope_binding_is_explicit_and_released() -> None:
    manager = SandboxManager(native_tools_enabled=False)
    scope = object()
    manager.bind_run_file_scope("run-1", "session-1", scope)
    assert manager.get_run_file_scope("run-1") is scope
    assert manager.get_active_run_file_scope("session-1") is scope
    with pytest.raises(RuntimeError, match="already bound"):
        manager.bind_run_file_scope("run-1", "session-1", object())
    manager.unbind_run_file_scope("run-1")
    assert manager.get_run_file_scope("run-1") is None
    assert manager.get_active_run_file_scope("session-1") is None

@pytest.mark.asyncio
async def test_p2_web_tool_registry_has_no_account_workspace_or_mcp_bypass(tmp_path: Path) -> None:
    class RemoteTools:
        def get_cached_tools(self):
            from langchain_core.tools import StructuredTool
            return [StructuredTool.from_function(name="remote_file_read",
                description="unsafe", func=lambda: "unsafe")]

    from uuid import uuid4

    from workspace.file_scope import RunFileScope

    manager = SandboxManager(
        native_tools_enabled=True, file_tools_enabled=True,
        host_bash_enabled=True, mcp_manager=RemoteTools(), nsjail_enabled=False,
        file_output_publisher=object(), file_conversion_provider=object(),
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    scope = RunFileScope(uuid4(), tmp_path / "inputs", tmp_path / "scratch",
                         tmp_path / "outputs", ())
    manager.bind_run_file_scope("run-1", "session-1", scope)
    manager.create_session_sandbox("session-1", str(repo),
        session_context={"account_id": "account-1", "channel_type": "web", "metadata": {}})
    sandbox = manager.get_sandbox_for_session("session-1")
    names = {item["function"]["name"] for item in await sandbox.list_tools()}
    assert {"list_run_candidates", "select_run_candidate", "read_file"} <= names
    assert names.isdisjoint({"Bash", "fs_read", "fs_write", "fs_edit", "Glob", "Grep",
                             "remote_file_read", "convert_file_to_pdf"})
