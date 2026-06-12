"""
MCP 多传输客户端 —— 支持 HTTP / SSE / stdio 三种传输方式连接 MCP Server。

架构:
  MCPSession       — Streamable HTTP：POST JSON-RPC 请求，直接返回响应
  MCPSessionSSE    — SSE 长连接：GET 建立流 + POST 发送请求
  MCPSessionStdio  — stdio 子进程：spawn 进程，stdin/stdout JSON-RPC
  MCPToolManager   — 多 server 管理器，启动时连接 + 缓存工具列表

配置格式:
  servers:
    # HTTP 连接
    fetch:
      url: "https://mcp.api-inference.modelscope.net/xxx/mcp"
      headers:
        Authorization: "Bearer ${TOKEN}"

    # stdio 子进程连接
    stock-sdk:
      transport: "stdio"
      command: "node"
      args: ["tools/mcp/stock-sdk/dist/cli.js", "mcp"]
      env:
        STOCK_SDK_MCP_TOOLS: "core"
"""
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from sandbox.tools.types import ToolResult

logger = logging.getLogger("HpAgent.MCP")

JSONRPC_VERSION = "2.0"

# ── 工具描述增强 ──────────────────────────────────────────────────
# 从 config/tool_enrich.yaml 加载，启动后缓存在此 dict 中。
# key: MCP tool name, value: 追加到 description 末尾的提示文本
_tool_enrich: dict = {}


# ── 数据结构 ────────────────────────────────────────────────────────

@dataclass
class CachedTool:
    """从 tools/list 响应中提取的工具定义缓存。"""
    name: str
    description: str
    input_schema: dict
    server_name: str
    result_transform: Optional[str] = None  # 结果后处理变换名，对应 RESULT_TRANSFORMS 的 key


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
                 timeout: float = 60.0, session_ttl: float = 1500):
        self._name = name
        self._url = url
        self._headers = dict(headers or {})
        self._headers.setdefault("Accept", "application/json, text/event-stream")
        self._headers.setdefault("Content-Type", "application/json")
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._connected = False
        self._session_id: Optional[str] = None
        self._session_established_at: float = 0.0
        self._session_ttl = session_ttl  # 秒，默认 1500s（匹配服务端实际 TTL）

    # ── 生命周期 ────────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 HTTP 客户端并完成 MCP 握手。"""
        if self._connected:
            return

        self._http = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(self._timeout),
        )

        # MCP 握手: initialize → 捕获 session ID → notified/initialized
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
        self._session_established_at = time.monotonic()
        logger.info("MCP '%s': connected (session=%s)", self._name, self._session_id or "none")

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

    # ── 会话保活 ──────────────────────────────────────────────

    async def _ensure_alive(self) -> None:
        """预检 session 是否即将过期，到期前主动重连。

        在每次 _send_request 前调用。避免首次工具调用时才发现过期
        而产生的 3s 重连尾延迟。

        两阶段检查：
        1. 超过 TTL → 无条件重连
        2. 超过 50% TTL → 轻量 ping 验证，失败则重连（服务端 TTL 可能更短）

        注意：connect() 内部也调 _send_request("initialize")，
        此时 _http 未创建，直接跳过避免递归。
        """
        if self._http is None:
            return  # connect() 尚未完成，跳过
        if not self._connected:
            return  # 已在重连过程中（_post_with_retry 处理），不干预

        age = time.monotonic() - self._session_established_at
        if age > self._session_ttl:
            logger.info(
                "MCP '%s': session aged %.0fs > TTL %.0fs, proactive reconnect",
                self._name, age, self._session_ttl,
            )
            self._connected = False
            self._session_id = None
            # 重建 HTTP 客户端（旧 session 已无效）
            await self.disconnect()
            await self.connect()
        elif age > self._session_ttl * 0.5:
            # 危险区：主动 ping 验证 session 是否仍有效
            # 避免服务端 TTL 短于客户端预估导致首次调用 401 失败
            ok = await self._try_ping()
            if not ok:
                logger.info(
                    "MCP '%s': session ping failed at age %.0fs (TTL=%.0fs), proactive reconnect",
                    self._name, age, self._session_ttl,
                )
                self._connected = False
                self._session_id = None
                await self.disconnect()
                await self.connect()

    async def _try_ping(self) -> bool:
        """轻量级 session 活性检查 —— 用小请求验证 session 仍有效。

        失败时静默返回 False（不抛异常），让调用方决定是否重连。
        """
        try:
            self._request_id += 1
            request = {
                "jsonrpc": JSONRPC_VERSION,
                "id": self._request_id,
                "method": "tools/list",
                "params": {},
            }
            await self._post_with_retry(request, retry=False)
            return True
        except Exception:
            return False

    # ── MCP JSON-RPC ────────────────────────────────────────────

    async def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求，直接返回响应中的 result。

        调用前预检 session 是否过期，过期则主动重连。
        """
        await self._ensure_alive()
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
        if not self._http:
            raise ConnectionError(f"MCP '{self._name}': not connected")

        request = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
            "params": params,
        }

        headers = {}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        async with self._lock:
            resp = await self._http.post(
                self._url,
                json=request,
                headers=headers if headers else None,
            )
        # 202 Accepted = 通知已接收（无响应体）
        if resp.status_code >= 400:
            body = await resp.aread()
            logger.warning(
                "MCP '%s': notification '%s' failed — HTTP %s: %s",
                self._name, method, resp.status_code, body[:200],
            )

    async def _post(self, payload: dict) -> dict:
        """POST JSON-RPC 消息到 MCP 端点，session 过期时自动重连并重试一次。"""
        if not self._http:
            raise ConnectionError(f"MCP '{self._name}': not connected")

        return await self._post_with_retry(payload, retry=True)

    async def _post_with_retry(self, payload: dict, retry: bool) -> dict:
        headers = {}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        async with self._lock:
            resp = await self._http.post(
                self._url,
                json=payload,
                headers=headers if headers else None,
            )

        # 捕获服务端返回的 session ID
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        if resp.status_code in (401, 403) and retry:
            body = await resp.aread()
            logger.warning(
                "MCP '%s': session expired (HTTP %s: %s), reconnecting...",
                self._name, resp.status_code, body[:200],
            )
            self._connected = False
            self._session_id = None
            try:
                await self.connect()
            except Exception as e:
                raise RuntimeError(
                    f"MCP '{self._name}': reconnection failed after session expiry: {e}"
                )
            # retry once with new session
            return await self._post_with_retry(payload, retry=False)

        if resp.status_code >= 400:
            body = await resp.aread()
            raise RuntimeError(
                f"MCP '{self._name}': HTTP {resp.status_code}: {body[:200]}"
            )

        # 202 / 204 无响应体
        if resp.status_code in (202, 204):
            return {}

        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return self._parse_sse(await resp.aread())
        return resp.json()

    @staticmethod
    def _parse_sse(body: bytes) -> dict:
        """从 SSE 响应中提取 JSON-RPC data。"""
        text = body.decode("utf-8", errors="replace")
        data = None
        for line in text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
        if data is None:
            raise RuntimeError(f"MCP SSE response had no data line: {text[:200]}")
        return json.loads(data)

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


# ── MCP SSE 传输会话 ────────────────────────────────────────────────

class MCPSessionSSE:
    """MCP SSE 传输客户端。

    SSE 传输使用两个 HTTP 连接：
      1. GET <url> → 建立 SSE 长连接，接收服务端推送
      2. POST <endpoint_url> → 发送 JSON-RPC 请求

    响应通过 SSE 流异步返回，客户端使用 Future 映射请求 ID。

    连接流程:
      GET → endpoint 事件 → POST initialize → 后台 reader → 就绪

    并发:
      asyncio.Lock 保证 POST 请求串行化。
    """

    def __init__(self, name: str, url: str, headers: dict = None,
                 timeout: float = 60.0, session_ttl: float = 1500):
        self._name = name
        self._url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._connected = False
        self._session_id: Optional[str] = None
        self._session_established_at: float = 0.0
        self._session_ttl = session_ttl

        # SSE 特有状态
        self._post_url: Optional[str] = None          # endpoint 事件返回的 POST 地址
        self._pending: Dict[int, asyncio.Future] = {}  # request_id → Future<response>
        self._reader_task: Optional[asyncio.Task] = None
        self._response: Optional[httpx.Response] = None
        self._endpoint_future: Optional[asyncio.Future] = None

    # ── 生命周期 ────────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 SSE 连接并完成 MCP 握手。"""
        if self._connected:
            return

        # SSE 请求头
        sse_headers = dict(self._headers)
        sse_headers["Accept"] = "text/event-stream"

        # SSE 长连接：仅设 connect 超时，读/写/池超时全关
        self._http = httpx.AsyncClient(
            headers=sse_headers,
            timeout=httpx.Timeout(
                connect=self._timeout,
                read=None,
                write=None,
                pool=None,
            ),
        )

        # 1. GET SSE 端点
        self._response = await self._http.send(
            self._http.build_request("GET", self._url),
            stream=True,
        )

        if self._response.status_code >= 400:
            body = await self._response.aread()
            raise RuntimeError(
                f"MCP '{self._name}': SSE connect failed — "
                f"HTTP {self._response.status_code}: {body[:200]}"
            )

        sid = self._response.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        # 2. 启动 SSE 流 reader，等待 endpoint 事件
        loop = asyncio.get_running_loop()
        self._endpoint_future = loop.create_future()
        self._reader_task = asyncio.create_task(self._sse_reader())

        try:
            endpoint = await asyncio.wait_for(self._endpoint_future, timeout=15.0)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"MCP '{self._name}': timed out waiting for endpoint event from SSE stream"
            )

        # 解析 endpoint URL（绝对或相对）
        parsed = urlparse(endpoint)
        if parsed.scheme:
            self._post_url = endpoint
        else:
            self._post_url = urljoin(self._url, endpoint)

        logger.info("MCP '%s': SSE endpoint discovered — %s", self._name, self._post_url)

        # 3. 初始化: POST initialize → 等待 SSE 响应
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

        # 4. 发送 initialized 通知
        await self._send_notification("notifications/initialized", {})

        self._connected = True
        self._session_established_at = time.monotonic()
        logger.info("MCP '%s': connected (session=%s)", self._name, self._session_id or "none")

    async def disconnect(self) -> None:
        """关闭 SSE 连接并清理资源。"""
        self._connected = False

        # 取消后台 reader
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._reader_task = None

        # 清理所有等待中的请求
        for req_id, future in list(self._pending.items()):
            if not future.done():
                future.set_exception(
                    RuntimeError(f"MCP '{self._name}': disconnected")
                )
        self._pending.clear()

        # 关闭 SSE 响应流
        if self._response:
            try:
                await self._response.aclose()
            except Exception:
                pass
            self._response = None

        # 关闭 HTTP 客户端
        if self._http:
            await self._http.aclose()
            self._http = None

        self._post_url = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # ── SSE 流 reader ──────────────────────────────────────────

    async def _sse_reader(self) -> None:
        """后台任务：持续读取 SSE 流并分发事件。"""
        event_type: Optional[str] = None
        try:
            async for line in self._response.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()

                    # endpoint 事件（仅启动时触发一次）
                    if (self._endpoint_future
                            and not self._endpoint_future.done()
                            and event_type == "endpoint"):
                        self._endpoint_future.set_result(data)
                        event_type = None
                        continue

                    # message 事件（或无 event: 前缀默认为 message）
                    if event_type in ("message", None):
                        try:
                            msg = json.loads(data)
                        except json.JSONDecodeError:
                            event_type = None
                            continue

                        msg_id = msg.get("id")
                        if msg_id is not None:
                            # 响应：匹配 Future
                            future = self._pending.pop(msg_id, None)
                            if future and not future.done():
                                future.set_result(msg)
                        else:
                            # 服务端通知（无 id）
                            if "method" in msg:
                                logger.debug(
                                    "MCP '%s': server notification — %s",
                                    self._name, msg.get("method"),
                                )
                elif line == "":
                    event_type = None
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._connected:
                logger.warning("MCP '%s': SSE reader error — %s", self._name, e)
        finally:
            # 流关闭时，清理所有未完成的 pending
            for req_id, future in list(self._pending.items()):
                if not future.done():
                    future.set_exception(
                        RuntimeError(f"MCP '{self._name}': SSE stream closed")
                    )
            self._pending.clear()

    # ── 请求发送 ────────────────────────────────────────────────

    async def _post_sse(self, request: dict) -> dict:
        """POST JSON-RPC 到 endpoint，通过 SSE 流等待响应。

        返回完整的 JSON-RPC 响应 dict（兼容基类 _post 行为）。
        """
        request_id = request["id"]
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future

        try:
            headers = {}
            if self._session_id:
                headers["mcp-session-id"] = self._session_id

            async with self._lock:
                resp = await self._http.post(
                    self._post_url,
                    json=request,
                    headers=headers if headers else None,
                )

            # 捕获服务端返回的 session ID
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid

            # session 过期
            if resp.status_code in (401, 403, 404):
                body = await resp.aread()
                raise RuntimeError(
                    f"MCP '{self._name}': session expired "
                    f"(HTTP {resp.status_code}: {body[:200]})"
                )

            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(
                    f"MCP '{self._name}': HTTP {resp.status_code}: {body[:200]}"
                )

            # 202/204 无响应体，响应通过 SSE 返回
            if resp.status_code in (202, 204):
                pass

            # 等待 SSE 流中的响应
            result = await asyncio.wait_for(future, timeout=self._timeout)
            return result

        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise RuntimeError(
                f"MCP '{self._name}': request {request_id} timed out after {self._timeout}s"
            )
        except Exception:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise

    async def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求，等待 SSE 响应返回 result。

        含 session 恢复：遇到 session expired 时自动重连并重试一次。
        """
        await self._ensure_alive()
        self._request_id += 1
        request = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        try:
            response = await self._post_sse(request)
        except RuntimeError as e:
            msg = str(e)
            if "session expired" in msg or "SSE stream closed" in msg:
                logger.warning(
                    "MCP '%s': reconnecting after — %s", self._name, msg[:120]
                )
                self._connected = False
                self._session_id = None
                await self.disconnect()
                await self.connect()
                # 重试一次
                self._request_id += 1
                request["id"] = self._request_id
                response = await self._post_sse(request)
            else:
                raise

        if "error" in response:
            err = response["error"]
            raise RuntimeError(
                f"MCP error {err.get('code', -1)}: {err.get('message', 'unknown')}"
            )

        return response.get("result", {})

    async def _send_notification(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（无 id），POST 后不等待响应。"""
        if not self._http or not self._post_url:
            raise ConnectionError(f"MCP '{self._name}': not connected")

        request = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
            "params": params,
        }

        headers = {}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        async with self._lock:
            resp = await self._http.post(
                self._post_url,
                json=request,
                headers=headers if headers else None,
            )
        if resp.status_code >= 400:
            body = await resp.aread()
            logger.warning(
                "MCP '%s': notification '%s' failed — HTTP %s: %s",
                self._name, method, resp.status_code, body[:200],
            )

    # ── 会话保活 ──────────────────────────────────────────────

    async def _ensure_alive(self) -> None:
        """检查 SSE 连接状态，必要时重连。

        检查顺序：
        1. reader 崩溃或 stream 关闭 → 重连
        2. 超过 TTL → 重连
        3. 超过 50% TTL → ping 验证
        """
        if self._http is None:
            return
        if not self._connected:
            return

        # 检查 SSE reader 是否存活
        reader_dead = (
            self._reader_task
            and self._reader_task.done()
            and self._reader_task.exception()
        )
        response_closed = (
            self._response is not None
            and hasattr(self._response, 'is_closed')
            and self._response.is_closed
        )

        if reader_dead or response_closed:
            logger.info(
                "MCP '%s': SSE stream lost (reader_dead=%s, response_closed=%s), reconnecting",
                self._name, reader_dead, response_closed,
            )
            self._connected = False
            self._session_id = None
            await self.disconnect()
            await self.connect()
            return

        age = time.monotonic() - self._session_established_at
        if age > self._session_ttl:
            logger.info(
                "MCP '%s': session aged %.0fs > TTL %.0fs, proactive reconnect",
                self._name, age, self._session_ttl,
            )
            self._connected = False
            self._session_id = None
            await self.disconnect()
            await self.connect()
        elif age > self._session_ttl * 0.5:
            ok = await self._try_ping()
            if not ok:
                logger.info(
                    "MCP '%s': session ping failed at age %.0fs (TTL=%.0fs), proactive reconnect",
                    self._name, age, self._session_ttl,
                )
                self._connected = False
                self._session_id = None
                await self.disconnect()
                await self.connect()

    async def _try_ping(self) -> bool:
        """轻量级 session 探活：通过 SSE 模式发送 tools/list 请求。

        失败时静默返回 False，不抛异常。
        """
        try:
            self._request_id += 1
            request = {
                "jsonrpc": JSONRPC_VERSION,
                "id": self._request_id,
                "method": "tools/list",
                "params": {},
            }
            await self._post_sse(request)
            return True
        except Exception:
            return False

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


# ── MCP stdio 传输会话 ───────────────────────────────────────────────

class MCPSessionStdio:
    """MCP stdio 传输客户端。

    通过子进程 stdin/stdout 通信 JSON-RPC（NDJSON 行分隔）。
    适用于本地 MCP server（如 stock-sdk-mcp），无需网络连接。

    通信模型:
      1. 启动子进程（command + args）
      2. stdin  写入 JSON-RPC 请求（每行一条 JSON）
      3. stdout 读取 JSON-RPC 响应（每行一条 JSON）
      4. stderr 后台读取，记入 DEBUG 日志

    并发:
      asyncio.Lock 保证请求串行化。对于 stdio 传输，响应按序返回，
      无需 id 匹配（id 仅做一致性校验）。

    配置格式:
      stock-sdk:
        transport: "stdio"
        command: "node"
        args: ["tools/mcp/stock-sdk/dist/cli.js", "mcp"]
        env:
          STOCK_SDK_MCP_TOOLS: "core"
    """

    def __init__(self, name: str, command: str, args: list = None,
                 env: dict = None, cwd: str = None, timeout: float = 60.0):
        self._name = name
        self._command = command
        self._args = list(args or [])
        self._cwd = cwd
        self._timeout = timeout

        # 环境变量：继承当前进程 + 配置覆盖
        self._env = dict(os.environ)
        if env:
            self._env.update(env)

        self._process = None  # asyncio.subprocess.Process
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._connected = False
        self._stderr_task: Optional[asyncio.Task] = None

    # ── 生命周期 ────────────────────────────────────────────────

    async def connect(self) -> None:
        """启动子进程并完成 MCP 握手。"""
        if self._connected:
            return

        logger.info(
            "MCP '%s': spawning — %s %s",
            self._name, self._command, " ".join(self._args),
        )

        self._process = await asyncio.create_subprocess_exec(
            self._command, *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )

        # 后台读取 stderr → 日志
        self._stderr_task = asyncio.create_task(self._read_stderr())

        # MCP 握手: initialize
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
        logger.info("MCP '%s': connected (stdio)", self._name)

    async def disconnect(self) -> None:
        """终止子进程并清理资源。"""
        self._connected = False

        # 取消 stderr reader
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
        self._stderr_task = None

        # 终止子进程
        if self._process:
            p = self._process
            self._process = None
            try:
                # 先关 stdin 通知进程退出，给 5s 优雅退出
                if p.stdin:
                    p.stdin.close()
                await asyncio.wait_for(p.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.debug("MCP '%s': process did not exit after 5s, killing", self._name)
                try:
                    p.kill()
                except Exception:
                    pass
                try:
                    await p.wait()
                except Exception:
                    pass
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            logger.info("MCP '%s': process terminated (code=%s)", self._name, p.returncode)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # ── stderr 后台读取 ───────────────────────────────────────

    async def _read_stderr(self) -> None:
        """后台读取子进程 stderr，记录到 DEBUG 日志。

        MCP stdio 协议规定 stdout 只输出协议消息，所有日志/诊断
        走 stderr。这里持续读取 stderr 避免缓冲区堵塞。
        """
        try:
            while self._process and self._process.stderr:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("MCP '%s' stderr: %s", self._name, text)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._connected:
                logger.debug("MCP '%s': stderr reader stopped — %s", self._name, e)

    # ── JSON-RPC 请求 ─────────────────────────────────────────

    async def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求并同步等待响应。

        写 stdin → 读 stdout，整个过程在锁内完成（保证串行化）。
        stdio 管道保证响应按发送顺序返回。
        """
        self._request_id += 1
        rid = self._request_id
        request = {
            "jsonrpc": JSONRPC_VERSION,
            "id": rid,
            "method": method,
            "params": params,
        }

        async with self._lock:
            # 检查进程存活
            if (
                self._process is None
                or self._process.returncode is not None
                or self._process.stdin is None
                or self._process.stdout is None
            ):
                code = self._process.returncode if self._process else "none"
                raise ConnectionError(
                    f"MCP '{self._name}': process not running (exit code={code})"
                )

            # 写 stdin
            payload = json.dumps(request, ensure_ascii=False) + "\n"
            self._process.stdin.write(payload.encode("utf-8"))
            await self._process.stdin.drain()

            # 读 stdout 响应
            try:
                line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"MCP '{self._name}': request '{method}' timed out "
                    f"after {self._timeout}s"
                )

            if not line:
                raise ConnectionError(
                    f"MCP '{self._name}': process stdout closed unexpectedly"
                )

            try:
                response = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as e:
                raw = line.decode("utf-8", errors="replace")[:200]
                raise RuntimeError(
                    f"MCP '{self._name}': invalid JSON response: {raw} — {e}"
                )

        # id 一致性校验（退出锁后）
        if response.get("id") != rid:
            logger.warning(
                "MCP '%s': response id mismatch (expected %d, got %s)",
                self._name, rid, response.get("id"),
            )

        if "error" in response:
            err = response["error"]
            raise RuntimeError(
                f"MCP error {err.get('code', -1)}: {err.get('message', 'unknown')}"
            )

        return response.get("result", {})

    async def _send_notification(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（无 id，不等待响应）。"""
        if (
            self._process is None
            or self._process.returncode is not None
            or self._process.stdin is None
        ):
            raise ConnectionError(f"MCP '{self._name}': not connected")

        request = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
            "params": params,
        }

        async with self._lock:
            payload = json.dumps(request, ensure_ascii=False) + "\n"
            self._process.stdin.write(payload.encode("utf-8"))
            await self._process.stdin.drain()

    # ── 会话保活 ──────────────────────────────────────────────

    async def _try_ping(self) -> bool:
        """轻量级进程活性检查。

        通过 ping 验证子进程是否仍存活并可正常响应。
        失败静默返回 False。
        """
        try:
            await self._send_request("ping", {})
            return True
        except Exception:
            return False

    # ── 工具操作 ────────────────────────────────────────────────

    async def list_tools(self) -> List[CachedTool]:
        """获取工具列表。"""
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
        """调用工具。"""
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

# ── 结果后处理变换注册表 ──────────────────────────────────────────────
# 签名: ToolResult → ToolResult
# 在 _call_remote 返回前调用，对 MCP 原始结果做结构化提取/清洗

def _transform_extract_content(result: ToolResult) -> ToolResult:
    """若 output 为 JSON 对象且含 "content" 键，只提取 content 值。"""
    if not result.success or not result.output:
        return result
    try:
        data = json.loads(result.output) if isinstance(result.output, str) else result.output
        if isinstance(data, dict) and "content" in data:
            result.output = data["content"]
    except (json.JSONDecodeError, TypeError):
        pass
    return result


RESULT_TRANSFORMS = {
    "extract_content": _transform_extract_content,
}


def _json_type_to_python(json_type: str):
    return {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "array": list, "object": dict,
    }.get(json_type, str)


def _build_langchain_tool(cached: CachedTool, session: "MCPSession | MCPSessionSSE | MCPSessionStdio", required: bool = False) -> StructuredTool:
    """从缓存的 MCP 工具定义构建 LangChain StructuredTool。"""
    schema = cached.input_schema
    properties = schema.get("properties", {})
    schema_required = set(schema.get("required", []))

    fields = {}
    for prop_name, prop_schema in properties.items():
        prop_type = _json_type_to_python(prop_schema.get("type", "string"))
        desc = prop_schema.get("description", "")
        if prop_name in schema_required:
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
        # ── LLM function calling 纠错：自动把 JSON 字符串参数转回列表/对象 ──
        # LLM 有时会把数组参数传成 JSON 字符串 "[\"600183\"]" 而非原生 list，
        # 导致 Pydantic 校验失败。这里按 schema 声明的类型自动转换。
        for prop_name, prop_schema in properties.items():
            val = kwargs.get(prop_name)
            if isinstance(val, str) and prop_schema.get("type") in ("array", "object"):
                try:
                    parsed = json.loads(val)
                    if (prop_schema["type"] == "array" and isinstance(parsed, list)) or \
                       (prop_schema["type"] == "object" and isinstance(parsed, dict)):
                        kwargs[prop_name] = parsed
                except (json.JSONDecodeError, TypeError):
                    pass  # 解析失败则保持原值，让 Pydantic 报原始错误

        result = await session.call_tool(cached.name, kwargs)
        # 结果后处理
        if cached.result_transform:
            transform = RESULT_TRANSFORMS.get(cached.result_transform)
            if transform:
                result = transform(result)
        return result

    desc = f"[{cached.server_name}] {cached.description}"
    enrich = _tool_enrich.get(cached.name, "")
    if enrich:
        desc += enrich

    return StructuredTool.from_function(
        name=cached.name,
        description=desc,
        args_schema=ArgsModel if fields else None,
        coroutine=_call_remote,
        metadata={
            "mcp_server": cached.server_name,
            "category": "mcp",
            # required 参数为布尔值，表示该工具是否始终加载
            "required": required,
        },
    )


# ── MCP 工具管理器 ──────────────────────────────────────────────────

class MCPToolManager:
    """多 MCP Server 连接管理器（Streamable HTTP 传输）。

    启动时连接所有 server → list_tools → 缓存为 LangChain StructuredTool。
    运行时工具列表不变。

    Usage:
        mgr = MCPToolManager("config/mcp/servers.yaml")
        await mgr.load_config()
        await mgr.connect()
        tools = mgr.get_cached_tools()
        await mgr.disconnect()
    """

    def __init__(self, config_path: str = "config/mcp/servers.yaml"):
        self._config_path = Path(config_path)
        self._sessions: Dict[str, "MCPSession | MCPSessionSSE | MCPSessionStdio"] = {}
        self._cached_tools: List[StructuredTool] = []
        self._config: Dict = {}
        self._keepalive_task: Optional[asyncio.Task] = None

    async def load_config(self) -> Dict:
        """加载 YAML 配置，替换 ${ENV_VAR} 占位符。"""
        global _tool_enrich

        if not self._config_path.exists():
            logger.warning("MCP config not found: %s", self._config_path)
            return {}

        raw = self._config_path.read_text(encoding="utf-8")
        raw = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), raw)
        self._config = yaml.safe_load(raw) or {}

        # ── 从 servers.yaml 提取工具描述增强（每个 server 下 tools.<name>.enrich） ──
        _tool_enrich.clear()
        servers = self._config.get("servers", {}) if isinstance(self._config, dict) else {}
        for server_cfg in servers.values():
            if not isinstance(server_cfg, dict):
                continue
            tools = server_cfg.get("tools", {})
            if not isinstance(tools, dict):
                continue
            for tool_name, tool_cfg in tools.items():
                if isinstance(tool_cfg, dict) and isinstance(tool_cfg.get("enrich"), str):
                    _tool_enrich[tool_name] = tool_cfg["enrich"]
        if _tool_enrich:
            logger.info("MCP tool enrich loaded: %d entries", len(_tool_enrich))

        return self._config

    async def connect(self) -> None:
        """连接所有 MCP server 并缓存工具列表（并行初始化以缩短启动时间）。"""
        servers_cfg = self._config.get("servers", {})
        if not servers_cfg:
            logger.info("No MCP servers configured")
            return

        # 收集待连接 server（跳过 disabled）
        targets: list[tuple[str, dict]] = []
        for name, cfg in servers_cfg.items():
            if cfg is None:
                continue
            if cfg.get("disabled", False):
                logger.info("MCP '%s': disabled, skipping", name)
                continue
            targets.append((name, cfg))

        async def _connect_and_cache(name: str, cfg: dict) -> None:
            """并行任务：连接 + list_tools + 缓存。异常由调用方处理。"""
            session = await self._connect_one(name, cfg)
            cached = await session.list_tools()
            truncate_limit = cfg.get("truncate_limit")
            result_transform = cfg.get("result_transform")
            is_required = cfg.get("required", False)
            # 批量追加到共享状态（并行安全：append + dict set 在 asyncio 协作调度下安全）
            for ct in cached:
                ct.result_transform = result_transform
                tool = _build_langchain_tool(ct, session, required=is_required)
                self._cached_tools.append(tool)
            self._sessions[name] = session
            logger.info("MCP '%s': %d tools cached", name, len(cached))

        # 并行连接所有 server（return_exceptions 让单个失败不影响其他）
        results = await asyncio.gather(
            *(_connect_and_cache(name, cfg) for name, cfg in targets),
            return_exceptions=True,
        )
        for (name, _cfg), r in zip(targets, results):
            if isinstance(r, Exception):
                logger.warning("MCP '%s': connection failed — %s", name, r)

        logger.info(
            "MCP connected: %d/%d servers, %d tools total",
            len(self._sessions), len(servers_cfg), len(self._cached_tools),
        )

        # 启动后台保活：每 30s ping 所有 session，防止服务端空闲断开
        self._start_keepalive()

    async def _connect_one(self, name: str, cfg: dict):
        transport = cfg.get("transport")

        # ── stdio 子进程传输 ──────────────────────────────────
        if transport == "stdio":
            command = cfg.get("command", "")
            if not command:
                raise ValueError(f"MCP '{name}': missing 'command' for stdio transport")
            # 若未指定 cwd，默认使用项目根目录（config/mcp/servers.yaml 的上三级），
            # 避免因 Python 进程的 CWD 不同导致 args 中的相对路径解析错误
            cwd = cfg.get("cwd")
            if not cwd:
                cwd = str(Path(self._config_path).parent.parent.parent.resolve())
            session = MCPSessionStdio(
                name=name,
                command=command,
                args=cfg.get("args", []),
                env=cfg.get("env"),
                cwd=cwd,
                timeout=cfg.get("request_timeout", 60.0),
            )
            await session.connect()
            return session

        # ── HTTP / SSE 传输 ───────────────────────────────────
        url = cfg.get("url", "")
        if not url:
            raise ValueError(f"MCP '{name}': missing 'url'")

        # 自动检测传输类型：显式 transport 字段 或 URL 以 /sse 结尾
        is_sse = (
            transport == "sse"
            or url.rstrip("/").endswith("/sse")
        )

        if is_sse:
            session = MCPSessionSSE(
                name=name,
                url=url,
                headers=cfg.get("headers", {}),
                timeout=cfg.get("request_timeout", 60.0),
                session_ttl=cfg.get("session_ttl", 1500),
            )
        else:
            session = MCPSession(
                name=name,
                url=url,
                headers=cfg.get("headers", {}),
                timeout=cfg.get("request_timeout", 60.0),
                session_ttl=cfg.get("session_ttl", 1500),
            )
        await session.connect()
        return session

    async def disconnect(self) -> None:
        """断开所有 MCP server 连接。"""
        self._stop_keepalive()
        for name in list(self._sessions):
            try:
                await self._sessions[name].disconnect()
                logger.info("MCP '%s': disconnected", name)
            except Exception as e:
                logger.warning("MCP '%s': disconnect error — %s", name, e)
        self._sessions.clear()
        self._cached_tools.clear()

    # ── 后台保活 ─────────────────────────────────────────────────

    _KEEPALIVE_INTERVAL = 30.0  # 秒

    def _start_keepalive(self) -> None:
        """启动后台保活协程。"""
        if self._keepalive_task is not None and not self._keepalive_task.done():
            return
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.debug("MCP keepalive started (interval=%.0fs)", self._KEEPALIVE_INTERVAL)

    def _stop_keepalive(self) -> None:
        """停止后台保活协程。"""
        if self._keepalive_task is not None and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            self._keepalive_task = None
            logger.debug("MCP keepalive stopped")

    async def _keepalive_loop(self) -> None:
        """后台循环：定期 ping 所有已连接的 session。

        每次 ping 都会触发 MCPSession._try_ping()，该函数内部调用 tools/list
        验证 session 活性。如果 ping 失败，MCPSession 会记录日志并触发
        _ensure_alive 中的主动重连流程（下次工具调用时）。

        这确保高延迟对话过程中 session 不会因空闲而服务端过期。
        """
        while True:
            try:
                await asyncio.sleep(self._KEEPALIVE_INTERVAL)
            except asyncio.CancelledError:
                break

            for name, session in list(self._sessions.items()):
                if not session._connected:
                    continue
                try:
                    # 使用 _try_ping（静默失败），下次 _ensure_alive 会处理重连
                    ok = await session._try_ping()
                    if ok:
                        session._session_established_at = time.monotonic()
                    else:
                        logger.debug("MCP '%s': keepalive ping failed", name)
                except Exception:
                    logger.debug("MCP '%s': keepalive ping error", name, exc_info=True)

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
