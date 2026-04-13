import logging
from src.core.config import AppConfig
from src.core.types import ReplyPayload
from src.context.session_store import SessionStore
from src.context.context_builder import build_context
from src.execution.llm_executor import ModelExecutor, ModelError
from src.response.payload_builder import build_reply_payload


logger = logging.getLogger(__name__)


def run_reply_agent(
    user_message: str,
    session_key: str,
    config: AppConfig,
    session_store: SessionStore,
    model_executor: ModelExecutor,
) -> ReplyPayload:
    """
    主流程：
    1. 调用 build_context 构建 TemplateContext
    2. 调用 model_executor.generate(context) 获取回复文本
    3. 若成功，将本轮对话存入 session_store.append_turn(...)
    4. 调用 build_reply_payload 返回结果
    5. 若模型调用失败，返回一个 is_error=True 的 ReplyPayload，且不存入历史
    """
    try:
        context = build_context(
            user_message=user_message,
            session_key=session_key,
            session_store=session_store,
            system_prompt=config.system_prompt,
            max_history_turns=config.max_history_turns,
        )

        model_response = model_executor.generate(context)

        session_store.append_turn(
            session_key=session_key,
            user_msg=user_message,
            assistant_msg=model_response,
        )

        return build_reply_payload(model_response, is_error=False)

    except ModelError as e:
        logger.error(f"Model error: {e}")
        return build_reply_payload(
            f"Sorry, I encountered an error: {str(e)}",
            is_error=True
        )
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return build_reply_payload(
            f"Sorry, an unexpected error occurred: {str(e)}",
            is_error=True
        )
