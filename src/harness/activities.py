"""
Temporal Activities —— 薄封装层，全部委托给 TurnOrchestrator。

Temporal 只做编排（何时调用），TurnOrchestrator 做一轮对话流程编排。
每条 Activity 是无状态的：依赖在 Worker 启动时通过 inject() 注入。

Activity 清单:
  1. process_turn_activity     → TurnOrchestrator.process_turn()
  2. archive_session_activity   → SessionStore.archive()
  3. reflect_activity           → TurnOrchestrator.reflect()
  4. reflect_batch_activity     → TurnOrchestrator.reflect() (批量)
  5. metrics_report_activity    → TurnOrchestrator.get_metrics()
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional, cast

from temporalio import activity

from harness.runner import TurnOrchestrator

# ═══════════════════════════════════════════════════════════════════════════════
# 模块级单例 —— 通过 inject() 在 Worker 启动时注入
# ═══════════════════════════════════════════════════════════════════════════════

_turn_orchestrator: Optional[TurnOrchestrator] = None
_qq_execution_host: Any = None
_qq_execution_host_enabled = False


def inject(turn_orchestrator: TurnOrchestrator) -> None:
    """在 Worker 启动前注入 TurnOrchestrator。

    Temporal Activity 要求函数无闭包状态，使用模块级变量。
    """
    global _turn_orchestrator
    _turn_orchestrator = turn_orchestrator


def inject_qq_execution_host(host: Any, *, enabled: bool) -> None:
    """Freeze the QQ implementation choice before any model/tool work begins."""
    if enabled and host is None:
        raise RuntimeError("QQ Execution Host feature flag has no Host")
    global _qq_execution_host, _qq_execution_host_enabled
    _qq_execution_host = host
    _qq_execution_host_enabled = enabled


def _orchestrator() -> TurnOrchestrator:
    if _turn_orchestrator is None:
        raise RuntimeError("TurnOrchestrator was not injected")
    return _turn_orchestrator


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 1: 处理对话轮次 —— 完整的 agentic loop
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def process_turn_activity(user_message: Dict[str, Any]) -> Dict[str, Any]:
    """处理一条用户消息的完整 agentic loop。

    TurnOrchestrator 内部完成:
      recall → context → model → tools → ... → response → retain

    Args:
        user_message: 用户消息 dict（content / sender_id / channel_type / ...）。

    Returns:
        {"content": str, "turns": int, "session_id": str, "account_id": str}
    """
    _activity_logger = logging.getLogger("HpAgent.Activity")
    sid = user_message.get("session_id", "?")
    t0 = time.monotonic()
    try:
        if _qq_execution_host_enabled:
            try:
                workflow_id = activity.info().workflow_id
            except RuntimeError:
                workflow_id = f"direct-{sid}"
            if not workflow_id:
                raise RuntimeError("QQ Activity has no Workflow identity")
            return cast(
                Dict[str, Any],
                await _qq_execution_host.execute(workflow_id, user_message),
            )
        result = await _orchestrator().process_turn(user_message)
        return cast(Dict[str, Any], result)
    except Exception:
        elapsed_ms = (time.monotonic() - t0) * 1000
        _activity_logger.exception(
            "process_turn_activity FAILED sid=%s latency=%.0fms", sid, elapsed_ms,
        )
        raise


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 2: 归档会话 —— 标记完成 + 清理活跃指针
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def archive_session_activity(session_id: str) -> Dict[str, Any]:
    """完整归档流程：读取事件 → 写 history.jsonl → fast 模型摘要 → 更新 meta.yaml。

    时序: SessionStore.archive() → write_history_jsonl() → generate_summary() → update_meta()

    Args:
        session_id: 会话 ID。

    Returns:
        {"ok": bool, "task_summary": str, "tags": [...], "event_count": int}
    """
    account_id = await _orchestrator().get_session_account(session_id)
    if not account_id:
        return {"ok": False, "error": f"Session not found: {session_id}"}
    try:
        return cast(
            Dict[str, Any],
            await _orchestrator().archive_session(session_id, account_id),
        )
    except Exception as e:
        logger = logging.getLogger("HpAgent.Activity")
        logger.exception("archive_session_activity FAILED sid=%s", session_id)
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 3: 记忆反思 —— 深度记忆推理（由 Temporal Schedule 定期触发）
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def reflect_activity(account_id: str) -> Dict[str, Any]:
    """触发深度记忆推理与知识抽象。

    由 Temporal Schedule 定期触发（建议每 6 小时）。
    TurnOrchestrator 委托给 SessionStore → Hindsight。

    Args:
        account_id: 统一账号 ID。

    Returns:
        {"insights": int}
    """
    return cast(Dict[str, Any], await _orchestrator().reflect(account_id))


@activity.defn
async def reflect_batch_activity(account_ids: List[str]) -> Dict[str, Any]:
    """批量触发所有活跃账号的记忆反思。

    由 Temporal Schedule 定期触发的 ReflectWorkflow 调用。
    遍历 account_ids，逐个调用 TurnOrchestrator.reflect()。

    Args:
        account_ids: 账号 ID 列表。

    Returns:
        {"results": {account_id: insights}, "total": int}
    """
    results: Dict[str, int] = {}
    for aid in account_ids:
        try:
            r = await _orchestrator().reflect(aid)
            results[aid] = r.get("insights", 0)
        except Exception:
            results[aid] = -1
    return {"results": results, "total": len(account_ids)}


# ═══════════════════════════════════════════════════════════════════════════════
# Activity 4: 指标报告 —— 输出结构化可观测性数据
# ═══════════════════════════════════════════════════════════════════════════════

@activity.defn
async def metrics_report_activity() -> Dict[str, Any]:
    """采集并输出 Hindsight 客户端可观测性指标。

    由 Temporal Schedule 定期触发（建议每 30 分钟）。
    输出结构化 JSON 日志供监控系统采集。

    Returns:
        HindsightMetrics.snapshot() 的完整指标快照。
    """
    metrics = await _orchestrator().get_metrics()
    _metrics_logger = logging.getLogger("HpAgent.Metrics")
    _metrics_logger.info(
        "HindsightMetrics|%s",
        json.dumps(metrics, ensure_ascii=False, default=str),
    )
    return cast(Dict[str, Any], metrics)
