from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from application.time_context import format_time_context
from orchestration.config import AgentConfig
from work_domain.time_policy import WorkTimeInPast, validate_new_timing


@pytest.mark.parametrize("timezone,expected,weekday", [
    ("Asia/Shanghai", "2027-01-01T00:00:01+08:00", "星期五"),
    ("America/New_York", "2026-12-31T11:00:01-05:00", "星期四"),
])
def test_context_local_calendar_and_original_message_anchor(timezone, expected, weekday):
    started = datetime(2026, 12, 31, 16, 0, 1, tzinfo=UTC)
    text = format_time_context(started, timezone, "2026-12-31T15:59:59+00:00")
    assert expected in text and weekday in text
    if timezone == "Asia/Shanghai":
        assert "本次用户消息发送时间：2026-12-31T23:59:59+08:00" in text
    assert "以该消息发送时间为基准" in text
    assert text == format_time_context(started, timezone, "2026-12-31T15:59:59+00:00")


def test_work_without_message_has_explicit_execution_anchor():
    text = format_time_context(datetime(2026, 10, 27, tzinfo=UTC), "Asia/Shanghai")
    assert "未提供原始消息时间" in text
    assert "已确定的任务时间保持原值" in text


def test_invalid_timezone_and_naive_clock_are_rejected():
    with pytest.raises(ValueError, match="agent_policy.default_timezone"):
        AgentConfig(default_timezone="invalid/zone")
    with pytest.raises(ValueError, match="timezone"):
        format_time_context(datetime(2026, 1, 1), "UTC")
    with pytest.raises(ValueError, match="message time"):
        format_time_context(datetime(2026, 1, 1, tzinfo=UTC), "UTC", "2026-01-01")


@pytest.mark.parametrize("delta", [-365 * 86400, -1, 0, 1])
def test_new_once_checks_instant_including_offset_and_boundary(delta):
    now = datetime(2026, 10, 27, 16, tzinfo=UTC)
    due = (now + timedelta(seconds=delta)).astimezone(ZoneInfo("Asia/Shanghai"))
    timing = {"kind": "once", "due_at": due.isoformat()}
    if delta > 0:
        validate_new_timing(timing, now=now)
    else:
        with pytest.raises(WorkTimeInPast) as caught:
            validate_new_timing(timing, now=now)
        assert caught.value.server_now == now.isoformat()
        assert caught.value.due_at == due.isoformat()


@pytest.mark.parametrize("kind", ["immediate", "daily"])
def test_other_timing_keeps_existing_semantics(kind):
    validate_new_timing({"kind": kind})
