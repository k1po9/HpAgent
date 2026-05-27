"""
核心抽象接口 —— 所有可替换组件的 ABC（抽象基类）。

设计意图：
  每一层对外暴露一个抽象接口，具体实现可以自由替换：
    - IResources: 资源管理层 —— 模型 API 凭据 + 退避链路
    - ISandbox: 沙箱执行层 —— 工具调用 + 健康检查
    - IChannel: 渠道通信层 —— NapCat / Web / Console 三种渠道

上层代码只依赖这些接口，不依赖具体实现类。

会话（Session）由 Temporal Workflow 管理，无需接口抽象层。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from common.types import Event, UnifiedMessage, SessionMetadata


class IResources(ABC):
    """外部资源访问接口 —— 模型 API 调用 + 凭据管理。

    实现类:
      - ResourcePool: 模型客户端注册、退避链管理、调用代理。
    """

    @abstractmethod
    async def initialize_models(self) -> None:
        """从凭据管理器加载所有模型端点并注册到内部客户端池。

        在 Worker 启动时调用一次。
        """
        ...

    @abstractmethod
    async def configure_fallback_group(self, group_name: str, model_ids: List[str]) -> None:
        """配置退避链 —— 主模型失败时按列表顺序尝试备用模型。

        Args:
            group_name: 退避组名，如 "default"。
            model_ids: 有序的模型 ID 列表。
        """
        ...

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model_selector: str = "default",
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Any:
        """调用模型生成回复。

        Args:
            messages: LLM 标准 messages 列表。
            model_selector: 模型选择器（退避组名或具体 model_id）。
            tools: 工具定义列表。
            stream: 是否启用流式返回。

        Returns:
            ModelResponse 或流式迭代器。
        """
        ...


class ISandbox(ABC):
    """沙箱接口 —— 所有外部操作（工具执行）通过沙箱代理。

    每个沙箱封装一组工具，对外提供统一的 execute 和 list_tools。
    架构中的 "双手"（hands）层。
    """

    @abstractmethod
    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """在沙箱中执行指定工具。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            工具执行结果（ToolResult 或原始值）。
        """
        ...

    @abstractmethod
    async def list_tools(self) -> List[Dict[str, Any]]:
        """列出沙箱中所有可用工具的 OpenAI 格式定义。

        Returns:
            [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}, ...]
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """沙箱健康检查 —— 返回 True 表示可用。"""
        ...


class IChannel(ABC):
    """渠道接口 —— 消息的入口（接收）和出口（发送）。

    每种渠道（NapCat/QQ、Web、Console）实现此接口，
    由 ChannelRouter 根据 UnifiedMessage.channel_type 路由。

    生命周期：
      1. 初始化 → 2. start_monitor(回调) → 3. 消息到达时通过回调写入系统
      4. 系统处理完 → 通过 send_message 发送回复 → 5. stop_monitor 停止
    """

    @abstractmethod
    async def normalize_message(self, raw_message: Any) -> UnifiedMessage:
        """将渠道原生消息格式标准化为 UnifiedMessage。

        Args:
            raw_message: 渠道原生消息（NapCat 收到 OneBot JSON，Console 收到字符串，Web 收到 dict）。

        Returns:
            统一的 UnifiedMessage 对象。
        """
        ...

    @abstractmethod
    async def send_message(self, message: UnifiedMessage) -> bool:
        """通过渠道发送回复消息。

        Args:
            message: 统一消息对象（含 channel_type、content、metadata 等）。

        Returns:
            发送是否成功。
        """
        ...

    @abstractmethod
    async def start_monitor(self, callback_url: str) -> bool:
        """启动消息监听。

        Args:
            callback_url: 消息回调，收到新消息时调用。
        """
        ...

    @abstractmethod
    async def stop_monitor(self) -> bool:
        """停止消息监听。"""
        ...

