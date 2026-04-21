from src.core.types import TemplateContext
from src.context.session_store import SessionStore


def build_context(
    user_message: str,
    session_key: str,
    session_store: SessionStore,
    system_prompt: str,
    max_history_turns: int,
) -> TemplateContext:
    """
    步骤：
    1. 从 session_store 获取历史
    2. 截断历史至 max_history_turns * 2 条消息（user+assistant 交替）
    3. 在历史最前方插入 system prompt（格式：{"role": "system", "content": ...}）
    4. 返回 TemplateContext，其中 conversation_history 为已处理的列表
    """
    history = session_store.get_history(session_key)

    max_messages = max_history_turns * 2
    truncated_history = history[-max_messages:] if len(history) > max_messages else history

    conversation_history = [{"role": "system", "content": system_prompt}] + truncated_history
    conversation_history.append({"role": "user", "content": user_message})

    return TemplateContext(
        body=user_message,
        session_key=session_key,
        conversation_history=conversation_history,
    )
