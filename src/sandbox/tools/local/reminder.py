"""
用户提醒工具 —— TaskScheduler 的 consumer。

作为本地工具注册到 Sandbox，LLM 可直接调用。
通过模块级单例 _scheduler 访问调度器（worker 启动时注入）。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger("HpAgent.Reminder")

# 模块级单例（worker 启动时通过 inject_scheduler 注入）
_scheduler: Optional["TaskScheduler"] = None


def inject_scheduler(scheduler: "TaskScheduler") -> None:
    """注入 TaskScheduler 实例（worker 启动时调用一次）。"""
    global _scheduler
    _scheduler = scheduler


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic 输入模型
# ═══════════════════════════════════════════════════════════════════════════════

class CreateReminderInput(BaseModel):
    content: str = Field(description="提醒内容文本")
    delay_minutes: Optional[int] = Field(
        default=None, description="N分钟后提醒（互斥于 at_time 和 cron_expr）"
    )
    at_time: Optional[str] = Field(
        default=None,
        description="指定时间（ISO 8601 格式，如 '2026-06-16T15:30:00'），互斥于 delay_minutes 和 cron_expr",
    )
    cron_expr: Optional[str] = Field(
        default=None,
        description="Cron 周期表达式（如 '0 8 * * *' 每天8点），互斥于 delay_minutes 和 at_time",
    )


class ListRemindersInput(BaseModel):
    status: str = Field(
        default="pending",
        description="按状态过滤：'pending'（待触发）、'triggered'（已触发）、'cancelled'（已取消）、空=全部",
    )


class CancelReminderInput(BaseModel):
    reminder_id: str = Field(description="要取消的提醒 ID")


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════

def _calc_trigger(
    delay_minutes: Optional[int],
    at_time: Optional[str],
) -> float:
    """根据参数计算触发时间戳，互斥校验。"""
    count = sum(1 for v in (delay_minutes, at_time) if v is not None)
    if count != 1:
        raise ValueError(
            "必须且只能指定 delay_minutes 或 at_time 之一（cron_expr 使用 cron 周期，不在此处计算）"
        )

    if delay_minutes is not None:
        if delay_minutes <= 0:
            raise ValueError("delay_minutes 必须大于 0")
        return time.time() + delay_minutes * 60

    if at_time is not None:
        try:
            dt = datetime.fromisoformat(at_time)
            ts = dt.timestamp()
            if ts <= time.time():
                raise ValueError(f"指定时间 {at_time} 已经过去")
            return ts
        except ValueError as e:
            if "已经过去" in str(e):
                raise
            raise ValueError(f"无效的 ISO 8601 时间格式: {at_time}") from e

    # unreachable
    return 0.0


def _ts_to_str(ts: float) -> str:
    """Unix 时间戳 → 可读字符串。"""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _ensure_scheduler() -> "TaskScheduler":
    """确保 scheduler 已注入。"""
    if _scheduler is None:
        raise RuntimeError(
            "TaskScheduler not injected. Call inject_scheduler() at worker startup."
        )
    return _scheduler


# ═══════════════════════════════════════════════════════════════════════════════
# 工具工厂函数
# ═══════════════════════════════════════════════════════════════════════════════

def create_reminder_tool(session_context: dict) -> StructuredTool:
    """创建「创建提醒」工具。

    Args:
        session_context: 包含 account_id, sender_id, channel_type, metadata 的字典。
    """

    async def _execute(
        content: str,
        delay_minutes: Optional[int] = None,
        at_time: Optional[str] = None,
        cron_expr: Optional[str] = None,
    ) -> str:
        scheduler = _ensure_scheduler()

        # 互斥校验
        provided = sum(
            1 for v in (delay_minutes, at_time, cron_expr) if v is not None
        )
        if provided != 1:
            raise ValueError(
                "必须且只能指定 delay_minutes、at_time 或 cron_expr 之一"
            )

        if cron_expr:
            # 周期任务：用 cron 表达式，计算首次触发时间
            try:
                from croniter import croniter
                from datetime import datetime as dt, timezone as tz
                now = dt.now(tz=tz.utc)
                cron = croniter(cron_expr, now)
                trigger_at = cron.get_next(float)
            except (ValueError, KeyError) as e:
                raise ValueError(f"无效的 cron 表达式: {cron_expr}") from e
        else:
            trigger_at = _calc_trigger(delay_minutes, at_time)

        from orchestration.scheduler import ScheduledTask

        task = ScheduledTask(
            task_type="user_reminder",
            trigger_at=trigger_at,
            cron_expr=cron_expr or "",
            params={
                "content": content,
                **session_context,
            },
        )

        task_id = await scheduler.schedule(task)
        logger.info("Reminder created: id=%s content=%s trigger=%s", task_id, content, _ts_to_str(trigger_at))
        return f"已创建提醒：{content} | 触发时间：{_ts_to_str(trigger_at)} | ID：{task_id}"

    return StructuredTool.from_function(
        name="create_reminder",
        description=(
            "创建一个定时提醒。支持三种方式（三选一）："
            "1) delay_minutes=N分钟后提醒；"
            "2) at_time=ISO 8601格式指定时间；"
            "3) cron_expr=cron周期表达式（如 '0 8 * * *' 每天8点）。"
            "到期后会在当前对话中推送提醒消息。"
        ),
        args_schema=CreateReminderInput,
        coroutine=_execute,
    )


def create_list_reminders_tool(session_context: dict) -> StructuredTool:
    """创建「列出提醒」工具。

    Args:
        session_context: 包含 account_id 等字段，用于过滤当前用户的提醒。
    """

    async def _execute(status: str = "pending") -> str:
        scheduler = _ensure_scheduler()
        account_id = session_context.get("account_id", "")

        tasks = scheduler.list_by_filter(
            task_type="user_reminder",
            status=status or "",
            account_id=account_id,
        )

        if not tasks:
            return f"当前没有{'状态为 ' + status if status else ''}的提醒。"

        lines = [f"{'状态为 ' + status + ' 的' if status else '所有'}提醒（共 {len(tasks)} 条）："]
        for task in tasks:
            cron_info = f" | 周期：{task.cron_expr}" if task.cron_expr else ""
            trigger_info = f" | 触发时间：{_ts_to_str(task.trigger_at)}" if task.status == "pending" else ""
            status_label = {
                "pending": "⏳",
                "triggered": "✓",
                "cancelled": "✗",
            }.get(task.status, task.status)
            lines.append(
                f"  [{status_label}] {task.id[:8]}... "
                f"{task.params.get('content', '(无内容)')}"
                f"{trigger_info}{cron_info}"
            )

        return "\n".join(lines)

    return StructuredTool.from_function(
        name="list_reminders",
        description="列出当前用户的所有提醒（可按状态过滤：pending/triggered/cancelled）",
        args_schema=ListRemindersInput,
        coroutine=_execute,
    )


def create_cancel_reminder_tool(session_context: dict) -> StructuredTool:
    """创建「取消提醒」工具。

    Args:
        session_context: 包含 account_id 等字段。
    """

    async def _execute(reminder_id: str) -> str:
        scheduler = _ensure_scheduler()
        account_id = session_context.get("account_id", "")

        # 查找任务，确保属于当前用户
        tasks = scheduler.list_by_filter(
            task_type="user_reminder",
            status="pending",
            account_id=account_id,
        )
        target = None
        for task in tasks:
            if task.id == reminder_id or task.id.startswith(reminder_id):
                target = task
                break

        if target is None:
            return f"未找到提醒 {reminder_id}（可能不存在或不属于你）"

        ok = await scheduler.cancel(target.id)
        if ok:
            logger.info("Reminder cancelled: id=%s content=%s", target.id, target.params.get("content", ""))
            return f"已取消提醒：{target.params.get('content', '(无内容)')}"
        return "取消失败，提醒可能已被取消或触发。"

    return StructuredTool.from_function(
        name="cancel_reminder",
        description="取消一个待触发的提醒。需要提供提醒 ID。",
        args_schema=CancelReminderInput,
        coroutine=_execute,
    )
