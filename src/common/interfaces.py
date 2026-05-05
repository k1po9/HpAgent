"""
核心抽象接口 —— 所有可替换组件的 ABC（抽象基类）。

设计意图：
  每一层对外暴露一个抽象接口，具体实现可以自由替换：
    - ISession: 会话记忆层 —— 支持 File / Temporal / PostgreSQL 三种实现
    - IResources: 资源管理层 —— 模型 API 凭据 + 退避链路
    - ISandbox: 沙箱执行层 —— 工具调用 + 健康检查
    - IChannel: 渠道通信层 —— NapCat / Web / Console 三种渠道
    - ITool: 工具定义层 —— 每个工具的名称、描述、参数和执行逻辑

上层代码只依赖这些接口，不依赖具体实现类。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from common.types import Event, UnifiedMessage, SessionMetadata


class ISession(ABC):
    """会话记忆接口 —— 存储和检索对话事件历史。

    实现类:
      - TemporalSessionManager: 通过 Temporal Workflow Queries 读取事件（当前使用）
      - (未来) 基于 storage.InfraContainer 的 PG/File 双后端 SessionManager
    """

    @abstractmethod
    async def create_session(self, metadata: SessionMetadata) -> str:
        """创建新会话，返回 session_id。"""
        ...

    @abstractmethod
    async def emit_event(self, event: Event) -> str:
        """向会话追加一条事件，返回 event_id。"""
        ...

    @abstractmethod
    async def get_events(
        self,
        session_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[Event]:
        """查询会话事件历史。

        Args:
            session_id: 会话 ID。
            offset: 偏移量（跳过的条数）。
            limit: 返回条数上限，None 表示不限制。
            event_types: 可选过滤，只返回指定类型的事件（如 ["user_message", "model_message"]）。
        """
        ...

    @abstractmethod
    async def rewind_session(self, session_id: str, target_event_id: str) -> Dict[str, Any]:
        """回滚会话到指定事件（删除该事件之后的所有事件）。

        Returns:
            包含 removed_events_count 的字典。
        """
        ...

    @abstractmethod
    async def archive_session(self, session_id: str) -> bool:
        """归档会话（标记为 archived 状态，不可再写入新事件）。"""
        ...

    @abstractmethod
    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[SessionMetadata]:
        """列出会话，支持分页和按状态/标签过滤。"""
        ...


class IResources(ABC):
    """外部资源访问接口 —— 模型 API 调用 + 凭据管理 + 代理请求。

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
    async def register_model(self, model_id: str, client: Any, priority: int = 0) -> None:
        """注册一个模型客户端。

        Args:
            model_id: 模型标识，如 "anthropic:claude-sonnet-4-6"。
            client: 模型客户端实例（需实现 generate 方法）。
            priority: 优先级，数字越小越优先。
        """
        ...

    @abstractmethod
    async def configure_fallback(self, group_name: str, primary: str, *fallbacks: str) -> None:
        """配置退避链 —— 主模型失败时按序尝试备用模型。

        Args:
            group_name: 退避组名，如 "default"。
            primary: 主模型 ID。
            *fallbacks: 备用模型 ID 序列。
        """
        ...

    @abstractmethod
    async def generate(
        self,
        model_selector: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Any:
        """调用模型生成回复。

        Args:
            model_selector: 模型选择器（退避组名或具体 model_id）。
            messages: LLM 标准 messages 列表。
            tools: 工具定义列表。
            stream: 是否启用流式返回。

        Returns:
            ModelResponse 或流式迭代器。
        """
        ...

    @abstractmethod
    async def get_credential(self, resource_id: str, scope: List[str]) -> str:
        """获取访问凭据（临时 token）。

        Args:
            resource_id: 资源标识。
            scope: 权限范围列表，如 ["model:invoke"]。

        Returns:
            临时 token 字符串。
        """
        ...

    @abstractmethod
    async def proxy_request(
        self,
        target_url: str,
        method: str,
        resource_id: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """代理 HTTP 请求（统一出口，支持认证注入）。

        Returns:
            包含 status_code / body / headers 的字典。
        """
        ...


class ISandbox(ABC):
    """沙箱接口 —— 所有外部操作（工具执行）通过沙箱代理。

    每个沙箱封装一组工具（ITool），对外提供统一的 execute 和 list_tools。
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


class ITool(ABC):
    """工具接口 —— 每个工具（calculator、web_search、file_read 等）实现此接口。

    属性（property）:
      - name: 工具唯一名称
      - description: 工具功能描述（给 LLM 看）
      - parameters: JSON Schema 格式的参数定义

    方法:
      - execute(**kwargs): 执行工具逻辑，返回 ToolResult
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一名称，沙箱内不可重复。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，会作为 LLM tool_use prompt 的一部分。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """参数 JSON Schema，如 {"type": "object", "properties": {...}, "required": [...]}。"""
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行工具。接收 keyword arguments，返回 ToolResult 或原始值。"""
        ...
