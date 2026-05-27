"""
MCP Streamable HTTP 客户端 —— 单端点 HTTP POST 连接远程 MCP Server。

架构:
  MCPSession      — POST JSON-RPC 请求，直接返回响应
  MCPToolManager  — 多 server 管理器，启动时连接 + 缓存工具列表

Streamable HTTP 传输:
  单 HTTP 端点，POST 发送 JSON-RPC 请求，响应体直接返回 JSON-RPC 结果。
  相比 SSE 模式，无需 GET 建立流、无需后台 reader、无需 endpoint 协商。

配置格式:
  servers:
    fetch:
      url: "https://mcp.api-inference.modelscope.net/xxx/mcp"
      headers:
        Authorization: "Bearer ${TOKEN}"
"""
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from sandbox.tools.types import ToolResult

logger = logging.getLogger("HpAgent.MCP")

JSONRPC_VERSION = "2.0"


# ── 数据结构 ────────────────────────────────────────────────────────

@dataclass
class CachedTool:
    """从 tools/list 响应中提取的工具定义缓存。"""
    name: str
    description: str
    input_schema: dict
    server_name: str


# ── MCP Streamable HTTP 会话 ────────────────────────────────────────

class MCPSession:
    """MCP Streamable HTTP 客户端。

    单 HTTP 端点:
      POST <url>  →  发送 JSON-RPC 请求，直接返回 JSON-RPC 响应。
      通知（notification）发送后不等待响应。

    并发:
      asyncio.Lock 保证请求串行化。
    """

    def __init__(self, name: str, url: str, headers: dict = None,
                 timeout: float = 60.0):
        self._name = name
        self._url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._connected = False

    # ── 生命周期 ────────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 HTTP 客户端并完成 MCP 握手。"""
        if self._connected:
            return

        self._http = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(self._timeout),
        )

        # MCP 握手: initialize → notified/initialized
        result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "HpAgent", "version": "1.0.0"},
        })

        server_info = result.get("serverInfo", {})
        logger.info(
            "MCP '%s': initialized — %s v%s (protocol %s)",
            self._name,
            server_info.get("name", "unknown"),
            server_info.get("version", "unknown"),
            result.get("protocolVersion", "unknown"),
        )

        await self._send_notification("notifications/initialized", {})
        self._connected = True
        logger.info("MCP '%s': connected", self._name)

    async def disconnect(self) -> None:
        """关闭 HTTP 客户端。"""
        self._connected = False
        if self._http:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # ── MCP JSON-RPC ────────────────────────────────────────────

    async def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求，直接返回响应中的 result。"""
        self._request_id += 1
        request = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        response = await self._post(request)

        if "error" in response:
            err = response["error"]
            raise RuntimeError(
                f"MCP error {err.get('code', -1)}: {err.get('message', 'unknown')}"
            )

        return response.get("result", {})

    async def _send_notification(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（无 id，不检查响应）。"""
        request = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
            "params": params,
        }
        await self._post(request)

    async def _post(self, payload: dict) -> dict:
        """POST JSON-RPC 消息到 MCP 端点。"""
        if not self._http:
            raise ConnectionError(f"MCP '{self._name}': not connected")

        async with self._lock:
            resp = await self._http.post(
                self._url,
                json=payload,
            )

        if resp.status_code >= 400:
            body = await resp.aread()
            raise RuntimeError(
                f"MCP '{self._name}': HTTP {resp.status_code}: {body[:200]}"
            )

        return resp.json()

    # ── 工具操作 ────────────────────────────────────────────────

    async def list_tools(self) -> List[CachedTool]:
        """获取远程工具列表。"""
        result = await self._send_request("tools/list", {})
        tools = []
        for td in result.get("tools", []):
            tools.append(CachedTool(
                name=td["name"],
                description=td.get("description", ""),
                input_schema=td.get("inputSchema", {}),
                server_name=self._name,
            ))
        logger.info("MCP '%s': %d tools listed", self._name, len(tools))
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
        """调用远程工具。"""
        try:
            result = await self._send_request("tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })

            content = result.get("content", [])
            output_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        output_parts.append(item.get("text", ""))
                    elif item.get("type") == "resource":
                        output_parts.append(json.dumps(item.get("resource", {})))
                    else:
                        output_parts.append(str(item))
                else:
                    output_parts.append(str(item))

            return ToolResult(
                success=not result.get("isError", False),
                output="\n".join(output_parts) if output_parts else str(result),
                metadata={"server": self._name, "tool": tool_name},
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e),
                metadata={"server": self._name, "tool": tool_name},
            )

    @property
    def is_connected(self) -> bool:
        return self._connected


# ── LangChain 工具构建 ─────────────────────────────────────────────

def _json_type_to_python(json_type: str):
    return {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "array": list, "object": dict,
    }.get(json_type, str)


def _build_langchain_tool(cached: CachedTool, session: MCPSession) -> StructuredTool:
    """从缓存的 MCP 工具定义构建 LangChain StructuredTool。"""
    schema = cached.input_schema
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    fields = {}
    for prop_name, prop_schema in properties.items():
        prop_type = _json_type_to_python(prop_schema.get("type", "string"))
        desc = prop_schema.get("description", "")
        if prop_name in required:
            fields[prop_name] = (prop_type, Field(description=desc))
        else:
            fields[prop_name] = (
                Optional[prop_type],
                Field(default=prop_schema.get("default"), description=desc),
            )

    if fields:
        model_name = f"mcp_{cached.server_name}_{cached.name}_args".replace("-", "_")
        try:
            ArgsModel = create_model(model_name, **fields)
        except Exception:
            ArgsModel = BaseModel
    else:
        ArgsModel = None

    async def _call_remote(**kwargs) -> ToolResult:
        return await session.call_tool(cached.name, kwargs)

    return StructuredTool.from_function(
        name=cached.name,
        description=f"[{cached.server_name}] {cached.description}",
        args_schema=ArgsModel if fields else None,
        coroutine=_call_remote,
        metadata={"mcp_server": cached.server_name, "category": "mcp"},
    )


# ── MCP 工具管理器 ──────────────────────────────────────────────────

class MCPToolManager:
    """多 MCP Server 连接管理器（Streamable HTTP 传输）。

    启动时连接所有 server → list_tools → 缓存为 LangChain StructuredTool。
    运行时工具列表不变。

    Usage:
        mgr = MCPToolManager("tools/definitions/mcp/servers.yaml")
        await mgr.load_config()
        await mgr.connect()
        tools = mgr.get_cached_tools()
        await mgr.disconnect()
    """

    def __init__(self, config_path: str = "tools/definitions/mcp/servers.yaml"):
        self._config_path = Path(config_path)
        self._sessions: Dict[str, MCPSession] = {}
        self._cached_tools: List[StructuredTool] = []
        self._config: Dict = {}

    async def load_config(self) -> Dict:
        """加载 YAML 配置，替换 ${ENV_VAR} 占位符。"""
        if not self._config_path.exists():
            logger.warning("MCP config not found: %s", self._config_path)
            return {}

        raw = self._config_path.read_text(encoding="utf-8")
        raw = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), raw)
        self._config = yaml.safe_load(raw) or {}
        return self._config

    async def connect(self) -> None:
        """连接所有 MCP server 并缓存工具列表。"""
        servers_cfg = self._config.get("servers", {})
        if not servers_cfg:
            logger.info("No MCP servers configured")
            return

        for name, cfg in servers_cfg.items():
            if cfg is None:
                continue
            if cfg.get("disabled", False):
                logger.info("MCP '%s': disabled, skipping", name)
                continue

            try:
                session = await self._connect_one(name, cfg)
                cached = await session.list_tools()
                for ct in cached:
                    self._cached_tools.append(_build_langchain_tool(ct, session))
                self._sessions[name] = session
                logger.info("MCP '%s': %d tools cached", name, len(cached))
            except Exception as e:
                logger.warning("MCP '%s': connection failed — %s", name, e)

        logger.info(
            "MCP connected: %d/%d servers, %d tools total",
            len(self._sessions), len(servers_cfg), len(self._cached_tools),
        )

    async def _connect_one(self, name: str, cfg: dict) -> MCPSession:
        url = cfg.get("url", "")
        if not url:
            raise ValueError(f"MCP '{name}': missing 'url'")

        session = MCPSession(
            name=name,
            url=url,
            headers=cfg.get("headers", {}),
            timeout=cfg.get("request_timeout", 60.0),
        )
        await session.connect()
        return session

    async def disconnect(self) -> None:
        """断开所有 MCP server 连接。"""
        for name in list(self._sessions):
            try:
                await self._sessions[name].disconnect()
                logger.info("MCP '%s': disconnected", name)
            except Exception as e:
                logger.warning("MCP '%s': disconnect error — %s", name, e)
        self._sessions.clear()
        self._cached_tools.clear()

    def get_cached_tools(self) -> List[StructuredTool]:
        return list(self._cached_tools)

    @property
    def server_count(self) -> int:
        return len(self._sessions)

    async def __aenter__(self):
        await self.load_config()
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()
