#!/usr/bin/env python3
"""Probe configured MCP servers without invoking business tools.

The probe performs only the MCP initialize handshake and ``tools/list``.  It
reports availability, connection/list latency, and static security annotations
derived from ``config/mcp/servers.yaml``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config/mcp/servers.yaml"
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
ALLOWED_SIDE_EFFECT_CLASSES = {
    "read_only",
    "idempotent_write",
    "non_idempotent_write",
}


@dataclass
class ToolHealth:
    name: str
    available: bool
    side_effect_class: str
    risk: str
    security_note: str
    configured: bool


@dataclass
class ServerHealth:
    name: str
    available: bool
    disabled: bool
    transport: str
    endpoint_security: str
    connect_ms: float | None = None
    list_tools_ms: float | None = None
    total_ms: float | None = None
    tool_count: int = 0
    tools: list[ToolHealth] = field(default_factory=list)
    configured_tools_not_advertised: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def _expand_env(value: Any, missing: set[str]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                missing.add(name)
                return ""
            return os.environ[name]

        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand_env(item, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item, missing) for item in value]
    return value


def load_config(path: Path) -> tuple[dict[str, Any], dict[str, set[str]]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("servers", {}), dict):
        raise ValueError("top-level 'servers' must be a mapping")

    expanded_servers: dict[str, Any] = {}
    missing_by_server: dict[str, set[str]] = {}
    for name, cfg in raw.get("servers", {}).items():
        if cfg is None:
            continue
        if not isinstance(cfg, dict):
            raise ValueError(f"server '{name}' must be a mapping")
        missing: set[str] = set()
        expanded_servers[str(name)] = _expand_env(cfg, missing)
        missing_by_server[str(name)] = missing
    raw["servers"] = expanded_servers
    return raw, missing_by_server


def endpoint_security(cfg: dict[str, Any]) -> tuple[str, list[str]]:
    transport = str(cfg.get("transport") or "http").lower()
    if transport == "stdio":
        return "local_process", [
            "stdio 会启动本地子进程；仅应配置受信任的 command、args、cwd 和 env。"
        ]

    url = str(cfg.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return "tls", []
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return "local_cleartext", ["本机 HTTP 未加密，仅适用于可信本机环境。"]
    if parsed.scheme == "http":
        return "insecure_cleartext", ["远程 HTTP 未加密，可能泄露请求、凭据和工具结果。"]
    return "unknown", ["无法确认端点传输安全性。"]


def annotate_tool(name: str, cfg: Any, available: bool = True) -> ToolHealth:
    configured = isinstance(cfg, dict)
    side_effect_class = cfg.get("side_effect_class") if configured else None
    if side_effect_class == "read_only":
        risk, note = "low", "声明为只读；检活不会调用该工具。"
    elif side_effect_class == "idempotent_write":
        risk, note = "medium", "可重复写操作；调用前仍需确认目标与权限。"
    elif side_effect_class == "non_idempotent_write":
        risk, note = "high", "非幂等写操作；重试可能造成重复副作用。"
    else:
        side_effect_class = "unknown"
        risk, note = "unreviewed", "缺少有效 side_effect_class，需人工审查。"
    return ToolHealth(
        name=name,
        available=available,
        side_effect_class=side_effect_class,
        risk=risk,
        security_note=note,
        configured=configured,
    )


def validate_security_config(cfg: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    tools = cfg.get("tools", {})
    if tools is not None and not isinstance(tools, dict):
        return ["tools 配置不是映射，所有工具将视为未审查。"]
    for name, tool_cfg in (tools or {}).items():
        if not isinstance(tool_cfg, dict):
            warnings.append(f"工具 {name} 配置无效，将视为未审查。")
            continue
        effect = tool_cfg.get("side_effect_class")
        if effect not in ALLOWED_SIDE_EFFECT_CLASSES:
            warnings.append(f"工具 {name} 缺少有效 side_effect_class。")
        key_arg = tool_cfg.get("idempotency_key_argument")
        if effect == "idempotent_write" and not key_arg:
            warnings.append(f"幂等写工具 {name} 未配置 idempotency_key_argument。")
    return warnings


def _redact_error(exc: BaseException, cfg: dict[str, Any]) -> str:
    message = f"{type(exc).__name__}: {exc}"
    secrets: list[str] = []
    for container_name in ("headers", "env"):
        container = cfg.get(container_name, {})
        if isinstance(container, dict):
            secrets.extend(str(value) for value in container.values() if value)
    for secret in sorted(secrets, key=len, reverse=True):
        message = message.replace(secret, "<redacted>")
    url = str(cfg.get("url") or "")
    if url:
        parsed = urlparse(url)
        safe_url = f"{parsed.scheme}://{parsed.hostname or '<host>'}"
        if parsed.port:
            safe_url += f":{parsed.port}"
        message = message.replace(url, safe_url + "/<redacted-path>")
    return message[:500]


def _build_session(name: str, cfg: dict[str, Any], config_path: Path, timeout: float):
    src_path = str(PROJECT_ROOT / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    from sandbox.tools.adapters.mcp import MCPSession, MCPSessionSSE, MCPSessionStdio

    transport = str(cfg.get("transport") or "").lower()
    if transport == "stdio":
        command = str(cfg.get("command") or "")
        if not command:
            raise ValueError("missing command for stdio transport")
        cwd = cfg.get("cwd") or str(config_path.resolve().parents[2])
        return MCPSessionStdio(
            name=name,
            command=command,
            args=cfg.get("args", []),
            env=cfg.get("env"),
            cwd=str(cwd),
            timeout=timeout,
        )

    url = str(cfg.get("url") or "")
    if not url:
        raise ValueError("missing url")
    session_cls = MCPSessionSSE if transport == "sse" or url.rstrip("/").endswith("/sse") else MCPSession
    return session_cls(
        name=name,
        url=url,
        headers=cfg.get("headers", {}),
        timeout=timeout,
        session_ttl=cfg.get("session_ttl", 1500),
    )


async def probe_server(
    name: str,
    cfg: dict[str, Any],
    config_path: Path,
    timeout: float,
    missing_env: set[str],
) -> ServerHealth:
    transport = "stdio" if cfg.get("transport") == "stdio" else (
        "sse" if cfg.get("transport") == "sse" or str(cfg.get("url", "")).rstrip("/").endswith("/sse") else "http"
    )
    endpoint_label, endpoint_warnings = endpoint_security(cfg)
    result = ServerHealth(
        name=name,
        available=False,
        disabled=bool(cfg.get("disabled", False)),
        transport=transport,
        endpoint_security=endpoint_label,
        warnings=endpoint_warnings + validate_security_config(cfg),
    )
    if result.disabled:
        result.error = "disabled by configuration"
        return result
    if missing_env:
        result.error = "missing environment variables: " + ", ".join(sorted(missing_env))
        return result

    started = time.perf_counter()
    session = None
    try:
        session = _build_session(name, cfg, config_path, timeout)
        connect_started = time.perf_counter()
        await asyncio.wait_for(session.connect(), timeout=timeout)
        result.connect_ms = round((time.perf_counter() - connect_started) * 1000, 1)

        list_started = time.perf_counter()
        discovered = await asyncio.wait_for(session.list_tools(), timeout=timeout)
        result.list_tools_ms = round((time.perf_counter() - list_started) * 1000, 1)
        configured_tools = cfg.get("tools", {}) if isinstance(cfg.get("tools", {}), dict) else {}
        discovered_names = {tool.name for tool in discovered}
        result.tools = [annotate_tool(tool.name, configured_tools.get(tool.name)) for tool in discovered]
        result.configured_tools_not_advertised = sorted(set(configured_tools) - discovered_names)
        if result.configured_tools_not_advertised:
            result.warnings.append("配置中的部分工具未被服务端公布，可能存在配置漂移。")
        result.tool_count = len(result.tools)
        result.available = True
    except Exception as exc:
        result.error = _redact_error(exc, cfg)
    finally:
        result.total_ms = round((time.perf_counter() - started) * 1000, 1)
        if session is not None:
            try:
                await asyncio.wait_for(session.disconnect(), timeout=5.0)
            except Exception as exc:
                result.warnings.append(f"断开连接失败：{_redact_error(exc, cfg)}")
    return result


async def run_probes(
    servers: dict[str, dict[str, Any]],
    config_path: Path,
    timeout: float,
    concurrency: int,
    missing_by_server: dict[str, set[str]],
) -> list[ServerHealth]:
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(name: str, cfg: dict[str, Any]) -> ServerHealth:
        async with semaphore:
            return await probe_server(name, cfg, config_path, timeout, missing_by_server.get(name, set()))

    return await asyncio.gather(*(bounded(name, cfg) for name, cfg in servers.items()))


def print_human(results: list[ServerHealth]) -> None:
    print("MCP 检活（只执行 initialize + tools/list，不调用业务工具）")
    print()
    for server in results:
        status = "可用" if server.available else ("已禁用" if server.disabled else "不可用")
        latency = f"{server.total_ms:.1f} ms" if server.total_ms is not None else "-"
        print(f"[{status}] {server.name}  transport={server.transport}  total={latency}  security={server.endpoint_security}")
        if server.connect_ms is not None:
            print(f"  latency: connect={server.connect_ms:.1f} ms, tools/list={server.list_tools_ms:.1f} ms")
        if server.error:
            print(f"  error: {server.error}")
        for warning in server.warnings:
            print(f"  warning: {warning}")
        for tool in server.tools:
            print(f"  - {tool.name}: available=yes, side_effect={tool.side_effect_class}, risk={tool.risk}")
        if server.configured_tools_not_advertised:
            print("  configured but not advertised: " + ", ".join(server.configured_tools_not_advertised))
        print()

    enabled = [item for item in results if not item.disabled]
    healthy = sum(item.available for item in enabled)
    unreviewed = sum(tool.risk == "unreviewed" for item in results for tool in item.tools)
    high = sum(tool.risk == "high" for item in results for tool in item.tools)
    print(f"汇总: servers={healthy}/{len(enabled)} available, tools={sum(item.tool_count for item in results)}, unreviewed={unreviewed}, high_risk={high}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全探测 MCP 可用性、延迟和工具副作用标注")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="MCP YAML 配置路径")
    parser.add_argument("--server", action="append", default=[], help="仅检查指定 server；可重复")
    parser.add_argument("--timeout", type=float, default=20.0, help="每个连接/请求的超时秒数（默认 20）")
    parser.add_argument("--concurrency", type=int, default=4, help="最大并发探测数（默认 4）")
    parser.add_argument("--include-disabled", action="store_true", help="在报告中包含 disabled server")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--strict-security", action="store_true", help="存在未审查工具或不安全远程 HTTP 时返回非零")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout <= 0 or args.concurrency <= 0:
        print("error: --timeout and --concurrency must be positive", file=sys.stderr)
        return 2
    try:
        config_path = args.config.resolve()
        config, missing_by_server = load_config(config_path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"error: cannot load MCP config: {exc}", file=sys.stderr)
        return 2

    servers = config.get("servers", {})
    if args.server:
        requested = set(args.server)
        unknown = sorted(requested - set(servers))
        if unknown:
            print("error: unknown server(s): " + ", ".join(unknown), file=sys.stderr)
            return 2
        servers = {name: cfg for name, cfg in servers.items() if name in requested}
    if not args.include_disabled:
        servers = {name: cfg for name, cfg in servers.items() if not cfg.get("disabled", False)}

    results = asyncio.run(
        run_probes(servers, config_path, args.timeout, args.concurrency, missing_by_server)
    )
    if args.json:
        payload = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "probe_policy": "initialize_and_tools_list_only",
            "results": [asdict(item) for item in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_human(results)

    if any(not item.available and not item.disabled for item in results):
        return 1
    if args.strict_security and any(
        item.endpoint_security in {"insecure_cleartext", "unknown"}
        or any(tool.risk == "unreviewed" for tool in item.tools)
        for item in results
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
