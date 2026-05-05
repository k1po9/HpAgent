"""
Temporal Activities —— Harness 层拆解后的大脑操作。

每条 Activity 封装一个非确定性操作（API 调用 / 工具执行 / I/O），
由 Temporal Workflow 通过 execute_activity 调用。
Activities 自身无状态 —— 全部依赖在 Worker 启动时通过 inject() 注入。

5 个 Activity 对应 agentic loop 的 5 个步骤：
  1. build_context_activity     → 事件历史 → LLM messages 列表
  2. get_available_tools_activity → 从所有活跃沙箱收集工具定义
  3. call_model_activity         → 调用 LLM（含退避）
  4. execute_tool_activity       → 在沙箱中执行工具
  5. send_response_activity      → 通过 ChannelRouter 发送最终回复

注入机制（inject 函数）：
  为避免闭包变量问题，使用模块级全局变量 _context_builder 等，
  在 Worker 启动时调用 inject() 一次性注入。
"""
from typing import List, Dict, Any, Optional

from temporalio import activity

from common.types import Event, ChannelType, EventType, UnifiedMessage

# ═══════════════════════════════════════════════════════════════════════════════
# 模块级单例 —— 通过 inject() 在 Worker 启动时注入
# ═══════════════════════════════════════════════════════════════════════════════

_context_builder = None      # HarnessContextBuilder 实例
_resource_pool = None        # ResourcePool 实例
_sandbox_manager = None      # SandboxManager 实例
_channel_router = None       # ChannelRouter 实例


def inject(
    context_builder=None,
    resource_pool=None,
    sandbox_manager=None,
    channel_router=None,
) -> None:
    """在 Worker 启动前注入共享依赖（仅调用一次）。

    这 4 个依赖会被所有 5 个 Activity 使用。
    Temporal Activity 要求函数无闭包状态，因此使用模块级变量而非闭包捕获。
    """
    global _context_builder, _resource_pool, _sandbox_manager, _channel_router
    _context_builder = context_builder
    _resource_pool = resource_pool
    _sandbox_manager = sandbox_manager
    _channel_router = channel_router


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 1: 构建上下文 —— events[] + channel_type → LLM messages[]
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def build_context_activity(
    events: List[Dict[str, Any]],
    channel_type: str = "",
) -> List[Dict[str, Any]]:
    """将事件历史 + 渠道信息组装为 LLM 标准 messages 列表。

    Workflow 传入的 events 是 dict 列表（Temporal 要求 JSON 可序列化），
    先转换为 Event dataclass 再交给 ContextBuilder 处理。

    Args:
        events: 事件历史列表 [{"type": "USER_MESSAGE", "content": "...", ...}, ...]。
        channel_type: 渠道字符串（"napcat" / "web" / "console"），空则从 events 自动检测。

    Returns:
        [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, ...]
    """
    event_objs = [Event.from_dict(e) if isinstance(e, dict) else e for e in events]
    ch_type = ChannelType(channel_type) if channel_type else None
    return _context_builder.build(events=event_objs, channel_type=ch_type)


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 2: 获取工具列表 —— 遍历所有沙箱收集工具定义
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def get_available_tools_activity() -> List[Dict[str, Any]]:
    """收集所有活跃沙箱中的工具定义。

    遍历 SandboxManager 中 status="active" 的沙箱，
    逐个调用 sandbox.list_tools()，汇总去重后返回。

    Returns:
        OpenAI 格式的工具定义列表，如:
        [{"type": "function", "function": {"name": "calculator", ...}}, ...]
    """
    tools = []
    for sandbox_info in _sandbox_manager.list_sandboxes():
        if sandbox_info["status"] != "active":
            continue
        sandbox = _sandbox_manager.get_sandbox(sandbox_info["sandbox_id"])
        tools.extend(await sandbox.list_tools())
    return tools


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 3: 调用模型 —— 通过 ResourcePool 的退避链调用 LLM
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def call_model_activity(
    context: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """调用 LLM 生成回复（自动退避）。

    ResourcePool 已配置退避链 —— 主模型失败时自动切换备用模型。

    Args:
        context: LLM 标准 messages 列表。
        tools: 工具定义列表（None 表示不带工具调用）。

    Returns:
        {"content": str, "tool_calls": [...], "stop_reason": str, "usage": {...}}
    """
    response = await _resource_pool.generate(
        messages=context,
        tools=tools if tools else None,
        stream=False,
    )
    # 将 ModelResponse dataclass 转换为 JSON 可序列化的 dict（Temporal 要求）
    return {
        "content": response.content,
        "tool_calls": [
            tc.to_dict() for tc in (response.tool_calls or [])
        ],
        "stop_reason": response.stop_reason.value,
        "usage": response.usage or {},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 4: 执行工具 —— 在沙箱中执行指定工具
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def execute_tool_activity(
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """在沙箱中执行工具并返回结果。

    遍历所有活跃沙箱，找到第一个注册了该工具的沙箱并执行。
    如果所有沙箱都没有该工具，返回错误。

    Args:
        tool_name: 工具名称（如 "calculator"）。
        arguments: 工具参数字典。

    Returns:
        {"output": Any, "error": str|None}
    """
    for sandbox_info in _sandbox_manager.list_sandboxes():
        if sandbox_info["status"] != "active":
            continue
        sandbox = _sandbox_manager.get_sandbox(sandbox_info["sandbox_id"])
        if sandbox.has_tool(tool_name):
            result = await sandbox.execute(tool_name, arguments)
            if hasattr(result, "to_dict"):
                return result.to_dict()
            return {"output": str(result), "error": None}
    return {"output": None, "error": f"Tool '{tool_name}' not found"}


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 5: 发送响应 —— 通过 ChannelRouter 路由回复到正确渠道
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def send_response_activity(
    content: str,
    user_message: Dict[str, Any],
) -> bool:
    """将模型最终回复通过 ChannelRouter 发送到对应用户渠道。

    从 user_message 中提取 channel_type，构造 UnifiedMessage，
    通过 ChannelRouter 路由到 NapCatChannel / ConsoleChannel / WebChannel。

    Args:
        content: 模型回复文本。
        user_message: 原始用户消息 dict（含 channel_type / sender_id / session_id / account_id）。

    Returns:
        True 表示发送成功，False 表示路由未找到或无 router。
    """
    if _channel_router is None:
        return False

    # 兼容字符串和枚举两种 channel_type 格式
    ch_type = user_message.get("channel_type", "console")
    if isinstance(ch_type, str):
        try:
            ch_type = ChannelType(ch_type)
        except ValueError:
            ch_type = ChannelType.CONSOLE

    msg = UnifiedMessage(
        session_id=user_message.get("session_id", ""),
        account_id=user_message.get("account_id", ""),
        sender_id=user_message.get("sender_id", ""),
        channel_type=ch_type,
        content=content,
        metadata=user_message.get("metadata", {}),
    )
    return await _channel_router.send(msg)
