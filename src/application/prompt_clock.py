"""Shanghai clock stamped onto a model call. Prompt files stay static."""
from datetime import datetime
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def current_time_line(now: datetime | None = None) -> str:
    moment = datetime.now(_SHANGHAI) if now is None else now
    if moment.tzinfo is None:
        raise ValueError("clock requires a timezone")
    return f"当前时间：{moment.astimezone(_SHANGHAI):%Y-%m-%d %H:%M}（Asia/Shanghai）"
