from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sandbox.sandbox_manager import SandboxManager
from sandbox.tools.local.file_analysis import FileToolLimits, create_file_analysis_tools
from workspace.file_scope import RunFileInput


def _scope(tmp_path: Path, content: bytes):
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)
    target = inputs / "service.log"
    target.write_bytes(content)
    item = RunFileInput(uuid4(), "service.log", len(content), "utf-8")
    return SimpleNamespace(inputs_root=inputs, inputs=(item,))


def _tools(scope, limits: FileToolLimits | None = None):
    return {
        tool.name: tool
        for tool in create_file_analysis_tools(lambda: scope, limits)
    }


async def test_inspect_file_returns_bounded_head_tail_and_metadata(tmp_path: Path) -> None:
    scope = _scope(tmp_path, b"head\nsecond\ntail\n")
    result = json.loads(await _tools(scope)["inspect_file"].ainvoke({
        "file": "service.log", "head_lines": 1, "tail_lines": 1,
        "max_sample_bytes": 1024,
    }))
    assert result["head"][0]["text"] == "head"
    assert result["tail"][0]["text"] == "tail"
    assert result["scanned_bytes"] == len(b"head\nsecond\ntail\n")
    assert result["returned_bytes"] == len(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    )


async def test_search_is_bounded_but_can_compute_total_when_requested(tmp_path: Path) -> None:
    content = b"".join(f"ERROR {index}\n".encode() for index in range(20))
    scope = _scope(tmp_path, content)
    tool = _tools(scope)["search_file"]
    bounded = json.loads(await tool.ainvoke({
        "file": "service.log", "query": "ERROR", "max_matches": 3,
        "need_total": False,
    }))
    assert len(bounded["matches"]) == 3
    assert bounded["truncated"] is True
    assert bounded["total_matches"] is None

    complete = json.loads(await tool.ainvoke({
        "file": "service.log", "query": "ERROR", "max_matches": 3,
        "need_total": True,
    }))
    assert complete["total_matches"] == 20
    assert len(complete["matches"]) == 3


async def test_count_matches_is_exact_across_chunk_boundaries(tmp_path: Path) -> None:
    content = b"x" * 65535 + b"ERROR and ERROR"
    scope = _scope(tmp_path, content)
    result = json.loads(await _tools(scope)["count_matches"].ainvoke({
        "file": "service.log", "query": "ERROR",
    }))
    assert result["count"] == 2
    assert result["scanned_bytes"] == len(content)


async def test_text_stats_has_bounded_dimensions(tmp_path: Path) -> None:
    content = b"INFO start\n\nWARN timeout\nERROR timeout\n"
    scope = _scope(tmp_path, content)
    result = json.loads(await _tools(scope)["text_stats"].ainvoke({
        "file": "service.log", "keywords": ["timeout"],
    }))
    assert result["total_lines"] == 4
    assert result["empty_lines"] == 1
    assert result["levels"] == {"ERROR": 1, "WARN": 1, "INFO": 1, "DEBUG": 0}
    assert result["keyword_counts"] == {"timeout": 2}


async def test_long_lines_do_not_expand_tool_output(tmp_path: Path) -> None:
    scope = _scope(tmp_path, b"A" * (2 * 1024 * 1024) + b"\n")
    limits = FileToolLimits(max_return_bytes=32 * 1024, max_line_bytes=4096)
    result_text = await _tools(scope, limits)["inspect_file"].ainvoke({
        "file": "service.log", "head_lines": 1, "tail_lines": 0,
        "max_sample_bytes": 16 * 1024,
    })
    result = json.loads(result_text)
    assert result["head"][0]["line_truncated"] is True
    assert len(result_text.encode()) < limits.max_return_bytes


async def test_tools_fail_without_an_active_run_scope() -> None:
    tool = _tools(None)["inspect_file"]
    try:
        await tool.ainvoke({"file": "service.log"})
    except ValueError as exc:
        assert "scope is unavailable" in str(exc)
    else:
        raise AssertionError("tool must fail closed without a Run scope")


async def test_web_sandbox_registers_file_tools_against_active_run_only(
    tmp_path: Path,
) -> None:
    scope = _scope(tmp_path / "files", b"ERROR\n")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager = SandboxManager(
        native_tools_enabled=False, nsjail_enabled=False, file_tools_enabled=True
    )
    manager.bind_run_file_scope("run-1", "session-1", scope)
    manager.create_session_sandbox(
        "session-1", str(workspace), session_context={
            "account_id": "account-1", "channel_type": "web", "metadata": {},
        }
    )
    sandbox = manager.get_sandbox_for_session("session-1")
    names = {item["function"]["name"] for item in await sandbox.list_tools()}
    assert {"inspect_file", "search_file", "count_matches", "text_stats"} <= names
    result, _audit = await sandbox.execute("count_matches", {
        "file": "service.log", "query": "ERROR",
    })
    assert result.success is True
    assert json.loads(result.output)["count"] == 1
    assert result.metadata["budget_usage"] == {
        "bytes_scanned": len(b"ERROR\n"),
        "bytes_returned_to_model": len(result.output.encode()),
    }
    assert result.metadata["trace_metadata"] == {"count": 1}
    manager.unbind_run_file_scope("run-1")
    failed, _audit = await sandbox.execute("inspect_file", {"file": "service.log"})
    assert failed.success is False
    assert "scope is unavailable" in str(failed.error)
