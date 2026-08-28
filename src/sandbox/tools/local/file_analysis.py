"""Bounded, structured read tools for a per-Run immutable input scope."""
from __future__ import annotations

import json
import os
import stat
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class FileToolLimits:
    max_return_bytes: int = 256 * 1024
    max_line_bytes: int = 64 * 1024
    max_scan_bytes: int = 512 * 1024 * 1024
    max_matches: int = 200
    max_keywords: int = 20


@dataclass
class _ScanState:
    scanned_bytes: int = 0
    scan_truncated: bool = False


class InspectFileInput(BaseModel):
    file: str
    head_lines: int = Field(default=20, ge=0, le=200)
    tail_lines: int = Field(default=20, ge=0, le=200)
    max_sample_bytes: int = Field(default=64 * 1024, ge=1, le=256 * 1024)


class SearchFileInput(BaseModel):
    file: str
    query: str = Field(min_length=1, max_length=1024)
    case_sensitive: bool = True
    max_matches: int = Field(default=50, ge=1, le=200)
    need_total: bool = False


class CountMatchesInput(BaseModel):
    file: str
    query: str = Field(min_length=1, max_length=1024)
    case_sensitive: bool = True


class TextStatsInput(BaseModel):
    file: str
    keywords: list[str] = Field(default_factory=list, max_length=20)


def create_file_analysis_tools(
    scope_provider: Callable[[], Any | None],
    limits: FileToolLimits | None = None,
) -> list[StructuredTool]:
    configured = limits or FileToolLimits()

    async def inspect_file(
        file: str, head_lines: int = 20, tail_lines: int = 20,
        max_sample_bytes: int = 64 * 1024,
    ) -> str:
        return _inspect(
            scope_provider, configured, file, head_lines, tail_lines,
            min(max_sample_bytes, configured.max_return_bytes // 2),
        )

    async def search_file(
        file: str, query: str, case_sensitive: bool = True,
        max_matches: int = 50, need_total: bool = False,
    ) -> str:
        return _search(
            scope_provider, configured, file, query, case_sensitive,
            min(max_matches, configured.max_matches), need_total,
        )

    async def count_matches(
        file: str, query: str, case_sensitive: bool = True,
    ) -> str:
        return _count(
            scope_provider, configured, file, query, case_sensitive,
        )

    async def text_stats(file: str, keywords: list[str] | None = None) -> str:
        return _stats(
            scope_provider, configured, file, keywords or [],
        )

    tools = [
        StructuredTool.from_function(
            name="inspect_file",
            description="Inspect safe metadata and bounded head/tail samples of a Run input file.",
            args_schema=InspectFileInput,
            coroutine=inspect_file,
        ),
        StructuredTool.from_function(
            name="search_file",
            description="Search a Run input file for a literal string with bounded returned matches.",
            args_schema=SearchFileInput,
            coroutine=search_file,
        ),
        StructuredTool.from_function(
            name="count_matches",
            description="Count literal matches across a complete Run input without returning lines.",
            args_schema=CountMatchesInput,
            coroutine=count_matches,
        ),
        StructuredTool.from_function(
            name="text_stats",
            description="Compute bounded line and keyword statistics for a Run input file.",
            args_schema=TextStatsInput,
            coroutine=text_stats,
        ),
    ]
    for tool in tools:
        tool.metadata = {
            "side_effect_class": "read_only",
            "file_scope_required": True,
            "budget_reservation": {
                "tool_calls": 1,
                "bytes_scanned": configured.max_scan_bytes,
                "bytes_returned_to_model": configured.max_return_bytes,
            },
            "usage_json_fields": {
                "scanned_bytes": "bytes_scanned",
                "returned_bytes": "bytes_returned_to_model",
            },
        }
        if tool.name == "inspect_file":
            tool.metadata["trace_json_fields"] = {"truncated": "truncated"}
        elif tool.name == "search_file":
            tool.metadata["trace_json_fields"] = {
                "total_matches": "match_count",
                "truncated": "truncated",
            }
        elif tool.name == "count_matches":
            tool.metadata["trace_json_fields"] = {"count": "count"}
        elif tool.name == "text_stats":
            tool.metadata["trace_json_fields"] = {"truncated": "truncated"}
    return tools


def _path(scope_provider: Callable[[], Any | None], logical_name: str) -> tuple[Any, Path]:
    scope = scope_provider()
    if scope is None:
        raise ValueError("Run file scope is unavailable")
    candidate = Path(logical_name)
    if (
        not logical_name or candidate.is_absolute() or len(candidate.parts) != 1
        or logical_name in {".", ".."} or "\x00" in logical_name
    ):
        raise ValueError("invalid logical file name")
    path = (scope.inputs_root / candidate).absolute()
    if not path.is_relative_to(scope.inputs_root) or path.is_symlink():
        raise ValueError("file escapes Run input scope")
    mode = os.stat(path, follow_symlinks=False).st_mode
    if not stat.S_ISREG(mode):
        raise ValueError("Run input is not a regular file")
    return scope, path


def _bounded_lines(
    path: Path, limits: FileToolLimits, state: _ScanState,
) -> Iterator[tuple[int, bytes, int, bool]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    line_number = 0
    sample = bytearray()
    line_length = 0
    overflow = False
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            while state.scanned_bytes < limits.max_scan_bytes:
                remaining = limits.max_scan_bytes - state.scanned_bytes
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                state.scanned_bytes += len(chunk)
                start = 0
                while start < len(chunk):
                    newline = chunk.find(b"\n", start)
                    end = len(chunk) if newline < 0 else newline
                    part = chunk[start:end]
                    line_length += len(part)
                    capacity = limits.max_line_bytes - len(sample)
                    if capacity > 0:
                        sample.extend(part[:capacity])
                    if len(part) > capacity:
                        overflow = True
                    if newline < 0:
                        break
                    line_number += 1
                    value = bytes(sample[:-1] if sample.endswith(b"\r") else sample)
                    yield line_number, value, line_length, overflow
                    sample.clear()
                    line_length = 0
                    overflow = False
                    start = newline + 1
            else:
                state.scan_truncated = bool(stream.read(1))
            if sample or line_length:
                line_number += 1
                yield line_number, bytes(sample), line_length, overflow
    finally:
        if fd >= 0:
            os.close(fd)


def _base(started: float, state: _ScanState, payload: dict[str, Any]) -> str:
    payload.update({
        "scanned_bytes": state.scanned_bytes,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    })
    payload["returned_bytes"] = 0
    while True:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        actual = len(encoded.encode("utf-8"))
        if payload["returned_bytes"] == actual:
            return encoded
        payload["returned_bytes"] = actual


def _inspect(provider, limits, file, head_lines, tail_lines, max_sample_bytes) -> str:
    started = time.monotonic()
    scope, path = _path(provider, file)
    state = _ScanState()
    head: list[dict[str, Any]] = []
    tail: deque[dict[str, Any]] = deque(maxlen=tail_lines)
    sample_bytes = 0
    line_count = 0
    for number, raw, _length, overflow in _bounded_lines(path, limits, state):
        line_count = number
        rendered = raw.decode("utf-8", errors="replace")
        item = {"line": number, "text": rendered, "line_truncated": overflow}
        item_bytes = len(rendered.encode("utf-8"))
        if len(head) < head_lines and sample_bytes + item_bytes <= max_sample_bytes:
            head.append(item)
            sample_bytes += item_bytes
        elif tail_lines and sample_bytes + item_bytes <= max_sample_bytes:
            tail.append(item)
    return _base(started, state, {
        "file": file, "size_bytes": path.stat().st_size,
        "encoding": next((item.encoding for item in scope.inputs if item.logical_name == file), "utf-8"),
        "head": head, "tail": list(tail), "lines_scanned": line_count,
        "full_scan": not state.scan_truncated,
        "truncated": state.scan_truncated or any(item["line_truncated"] for item in head) or any(item["line_truncated"] for item in tail),
    })


def _search(provider, limits, file, query, case_sensitive, max_matches, need_total) -> str:
    started = time.monotonic()
    _scope, path = _path(provider, file)
    state = _ScanState()
    needle = query if case_sensitive else query.casefold()
    matches: list[dict[str, Any]] = []
    total = 0
    returned_text_bytes = 0
    return_truncated = False
    for number, raw, _length, overflow in _bounded_lines(path, limits, state):
        text = raw.decode("utf-8", errors="replace")
        candidate = text if case_sensitive else text.casefold()
        if needle in candidate:
            total += 1
            item_bytes = len(text.encode("utf-8")) + 128
            if (
                len(matches) < max_matches
                and returned_text_bytes + item_bytes <= limits.max_return_bytes // 2
            ):
                matches.append({"line": number, "text": text, "line_truncated": overflow})
                returned_text_bytes += item_bytes
            else:
                return_truncated = True
                if not need_total:
                    state.scan_truncated = True
                    break
    return _base(started, state, {
        "file": file, "matches": matches,
        "total_matches": total if need_total and not state.scan_truncated else None,
        "truncated": state.scan_truncated or return_truncated or total > len(matches),
    })


def _count(provider, limits, file, query, case_sensitive) -> str:
    started = time.monotonic()
    _scope, path = _path(provider, file)
    pattern = query.encode("utf-8")
    if not case_sensitive and not query.isascii():
        raise ValueError("case-insensitive exact count currently requires an ASCII query")
    normalized = pattern if case_sensitive else pattern.lower()
    overlap = b""
    count = 0
    state = _ScanState()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            while state.scanned_bytes < limits.max_scan_bytes:
                remaining = limits.max_scan_bytes - state.scanned_bytes
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                state.scanned_bytes += len(chunk)
                data = overlap + (chunk if case_sensitive else chunk.lower())
                start = 0
                while (found := data.find(normalized, start)) >= 0:
                    if found + len(normalized) > len(overlap):
                        count += 1
                    start = found + max(1, len(normalized))
                overlap = data[-(len(normalized) - 1):] if len(normalized) > 1 else b""
            else:
                state.scan_truncated = bool(stream.read(1))
    finally:
        if fd >= 0:
            os.close(fd)
    if state.scan_truncated:
        raise ValueError("file_processing_limit: exact count exceeds scan limit")
    return _base(started, state, {
        "file": file, "count": count, "truncated": False,
    })


def _stats(provider, limits, file, keywords) -> str:
    started = time.monotonic()
    _scope, path = _path(provider, file)
    if len(keywords) > limits.max_keywords or any(not item or len(item) > 256 for item in keywords):
        raise ValueError("file_processing_limit: invalid keyword list")
    state = _ScanState()
    total_lines = empty_lines = max_line_bytes = 0
    levels = {name: 0 for name in ("ERROR", "WARN", "INFO", "DEBUG")}
    counts = {item: 0 for item in keywords}
    for _number, raw, length, overflow in _bounded_lines(path, limits, state):
        if overflow and (keywords or levels):
            raise ValueError("file_processing_limit: a line exceeds the statistics limit")
        total_lines += 1
        empty_lines += int(not raw.strip())
        max_line_bytes = max(max_line_bytes, length)
        text = raw.decode("utf-8", errors="replace")
        for level in levels:
            levels[level] += text.count(level)
        for keyword in counts:
            counts[keyword] += text.count(keyword)
    if state.scan_truncated:
        raise ValueError("file_processing_limit: statistics exceed scan limit")
    return _base(started, state, {
        "file": file, "total_lines": total_lines, "empty_lines": empty_lines,
        "max_line_bytes": max_line_bytes, "levels": levels,
        "keyword_counts": counts, "truncated": False,
    })
