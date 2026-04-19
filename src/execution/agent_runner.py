import logging
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional
from src.core.config import AppConfig
from src.core.types import TemplateContext, ReplyPayload
from src.context.session_store import SessionStore
from src.context.context_builder import build_context
from src.execution.harness.loop import AgentLoop, LoopConfig
from src.execution.harness.events import ExecutionEvent, EventType
from src.response.payload_builder import build_reply_payload
from src.model.client import ModelClient
from src.tools.service import ToolService


logger = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    payload: ReplyPayload
    events: list[ExecutionEvent]
    turns: int
    tool_calls_count: int


class AgentRunner:
    def __init__(
        self,
        config: AppConfig,
        session_store: SessionStore,
        tool_service: ToolService,
    ):
        self.config = config
        self.session_store = session_store
        self.tool_service = tool_service

        self.model_client = ModelClient(config.model)
        self.loop = AgentLoop(
            model_client=self.model_client,
            tool_service=self.tool_service,
            loop_config=self.config.loop,
        )

    async def run(
        self,
        context: TemplateContext,
        on_event: Optional[Callable[[ExecutionEvent], Awaitable[None]]] = None,
    ) -> AgentRunResult:
    
        messages = context.conversation_history.copy()

        final_text, events = await self.loop.run(
            messages=messages,
            on_event=on_event,
        )

        self.session_store.append_turn(
            context.session_key,
            user_msg=context.body,
            assistant_msg=final_text,
        )

        return AgentRunResult(
            payload=ReplyPayload(text=final_text),
            events=events,
            turns=len([e for e in events if e.type == EventType.TURN_COMPLETED]),
            tool_calls_count=len([e for e in events if e.type == EventType.TOOL_CALL_COMPLETED]),
        )


def run_reply_agent(
    config: AppConfig,
    session_store: SessionStore,
    user_message: str,
    tool_service: ToolService,
    session_key: str = "default",
    on_event: Optional[Callable[[ExecutionEvent], Awaitable[None]]] = None,
) -> ReplyPayload:
    """
    运行回复代理，根据用户消息生成回复。

    :param config: 配置对象
    :param session_store: 会话存储对象
    :param user_message: 用户消息
    :param tool_service: 工具服务对象
    :param session_key: 会话密钥
    :return: ReplyPayload 对象
    """
    try:
        context = build_context(
            user_message=user_message,
            session_key=session_key,
            session_store=session_store,
            system_prompt=config.system_prompt,
            max_history_turns=config.max_history_turns,
        )

        runner = AgentRunner(config, session_store, tool_service)
        import asyncio
        result = asyncio.run(runner.run(context, on_event))
        return result.payload

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return build_reply_payload(
            f"Sorry, an unexpected error occurred: {str(e)}",
            is_error=True
        )
