"""Explicit, replay-stable temporal context for an Agent execution."""
from datetime import datetime
from zoneinfo import ZoneInfo


def format_time_context(started_at: datetime, timezone: str, message_created_at: str = "") -> str:
    if started_at.utcoffset() is None:
        raise ValueError("context time must include timezone")
    zone = ZoneInfo(timezone)
    local = started_at.astimezone(zone)
    weekday = "一二三四五六日"[local.weekday()]
    lines = [
        "## 时间上下文",
        f"本次执行上下文开始时间：{local.isoformat()}（星期{weekday}）",
        f"默认时区：{timezone}",
        "上述时间是本次执行的固定基准；重试复用，不代表后续每轮请求的实时时钟。",
    ]
    if message_created_at:
        message_time = datetime.fromisoformat(message_created_at)
        if message_time.utcoffset() is None:
            raise ValueError("message time must include timezone")
        lines.extend([
            f"本次用户消息发送时间：{message_time.astimezone(zone).isoformat()}",
            "理解本次用户原话中的今天、明天、下周、几分钟后等相对时间，以该消息发送时间为基准。",
        ])
    else:
        lines.append("未提供原始消息时间；相对时间以本次执行上下文开始时间为基准，已确定的任务时间保持原值。")
    lines.extend([
        "用户明确指定的日期、年份和时区优先；否则使用上述时间基准和默认时区，不得依据训练知识猜测当前年份。",
        "未指定年份的未来安排选择符合表达的下一次日期；存在歧义时先澄清，不得擅自改变明确指定的过去日期。",
        "一次性定时必须提交带显式偏移的 due_at 和 IANA timezone；daily 使用本地 HH:MM。",
        "若服务器返回 work_time_in_past，操作未提交：核对日期和时区，明确过去日期须询问是否现在执行；不得自动加一年或改为立即执行。",
    ])
    return "\n".join(lines)
