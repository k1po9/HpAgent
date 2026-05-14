"""
Temporal Activities —— 薄封装层，全部委托给 HarnessRunner。

Temporal 只做编排（何时调用），HarnessRunner 做执行（如何调用）。
每条 Activity 是无状态的：依赖在 Worker 启动时通过 inject() 注入。

Activity 清单:
  1. process_turn_activity   → HarnessRunner.process_turn()
  2. archive_session_activity → SessionStore.archive()
  3. reflect_activity         → HarnessRunner.reflect()
"""
from typing import Dict, Any, Optional

from temporalio import activity

from harness.runner import HarnessRunner

# ═══════════════════════════════════════════════════════════════════════════════
# 模块级单例 —— 通过 inject() 在 Worker 启动时注入
# ═══════════════════════════════════════════════════════════════════════════════

_harness: Optional[HarnessRunner]=None       # HarnessRunner 实例


def inject(harness=None) -> None:
    """在 Worker 启动前注入 HarnessRunner。

    Temporal Activity 要求函数无闭包状态，使用模块级变量。
    """
    global _harness
    _harness = harness


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 1: 处理对话轮次 —— 完整的 agentic loop
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def process_turn_activity(user_message: Dict[str, Any]) -> Dict[str, Any]:
    """处理一条用户消息的完整 agentic loop。

    HarnessRunner 内部完成:
      recall → context → model → tools → ... → response → retain

    Args:
        user_message: 用户消息 dict（content / sender_id / channel_type / ...）。

    Returns:
        {"content": str, "turns": int, "session_id": str, "account_id": str}
    """
    
    return await _harness.process_turn(user_message)


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 2: 归档会话 —— 标记完成 + 清理活跃指针
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def archive_session_activity(session_id: str) -> Dict[str, Any]:
    """归档会话。在 Workflow 结束时调用。

    Args:
        session_id: 会话 ID。

    Returns:
        {"ok": bool}
    """
    try:
        await _harness._session.archive(session_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 3: 记忆反思 —— 深度记忆推理（由 Temporal Schedule 定期触发）
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def reflect_activity(account_id: str) -> Dict[str, Any]:
    """触发深度记忆推理与知识抽象。

    由 Temporal Schedule 定期触发（建议每 6 小时）。
    HarnessRunner 委托给 SessionStore → Hindsight。

    Args:
        account_id: 统一账号 ID。

    Returns:
        {"insights": int}
    """
    return await _harness.reflect(account_id)
