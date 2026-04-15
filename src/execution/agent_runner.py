import logging
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional
from src.core.config import AppConfig
from src.core.types import TemplateContext, ReplyPayload
from src.context.session_store import SessionStore
from src.context.context_builder import build_context
from src.execution.harness.loop import AgentLoop, LoopConfig
from src.execution.harness.events import ExecutionEvent, EventType
from src.execution.tools.registry import ToolRegistry
from src.execution.tools.router import ToolRouter
from src.execution.model.client import ModelClient
from src.response.payload_builder import build_reply_payload


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
        tool_registry: Optional[ToolRegistry] = None,
    ):
        self.config = config
        self.session_store = session_store
        self.tool_registry = tool_registry or ToolRegistry()

        self.model_client = ModelClient(
            api_key=config.model.api_key,
            base_url=config.model.base_url,
            model=config.model.model,
        )
        self.tool_router = ToolRouter(self.tool_registry)
        self.loop = AgentLoop(
            model_client=self.model_client,
            tool_router=self.tool_router,
            config=LoopConfig(max_turns=config.max_turns),
        )

    async def run(
        self,
        context: TemplateContext,
        on_event: Optional[Callable[[ExecutionEvent], Awaitable[None]]] = None,
    ) -> AgentRunResult:
        messages = context.conversation_history.copy()
        if context.body:
            messages.append({"role": "user", "content": context.body})

        final_text, events = await self.loop.run(
            messages=messages,
            tools=self.tool_registry.list_definitions(),
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
    user_message: str,
    session_key: str,
    config: AppConfig,
    session_store: SessionStore,
    model_executor=None,
) -> ReplyPayload:
    """
    兼容第一版的主流程函数。
    如果传入 model_executor，则使用第一版逻辑。
    否则使用新的 AgentRunner。
    """
    try:
        context = build_context(
            user_message=user_message,
            session_key=session_key,
            session_store=session_store,
            system_prompt=config.system_prompt,
            max_history_turns=config.max_history_turns,
        )

        if model_executor:
            model_response = model_executor.generate(context)
            session_store.append_turn(
                session_key=session_key,
                user_msg=user_message,
                assistant_msg=model_response,
            )
            return build_reply_payload(model_response, is_error=False)
        else:
            runner = AgentRunner(config, session_store)
            import asyncio
            result = asyncio.run(runner.run(context))
            return result.payload

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return build_reply_payload(
            f"Sorry, an unexpected error occurred: {str(e)}",
            is_error=True
        )
