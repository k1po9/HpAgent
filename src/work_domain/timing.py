"""Explicit UTC timing: ambiguous daily times use first fold; gaps skip that day."""
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def daily_on(day, timing):
    zone = ZoneInfo(timing['timezone'])
    local = datetime.combine(day, time.fromisoformat(timing['local_time']), zone).replace(fold=0)
    utc = local.astimezone(UTC)
    return utc if utc.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None) else None


def next_daily(timing, after):
    day = after.astimezone(ZoneInfo(timing['timezone'])).date()
    for offset in range(4):
        value = daily_on(day + timedelta(days=offset), timing)
        if value is not None and value > after:
            return value
    raise ValueError('daily time has no resolvable occurrence')


def latest_daily(timing, now):
    day = now.astimezone(ZoneInfo(timing['timezone'])).date()
    for offset in range(4):
        value = daily_on(day - timedelta(days=offset), timing)
        if value is not None and value <= now:
            return value
    raise ValueError('daily time has no resolvable occurrence')


def initial_continuation(timing, now=None):
    from work_domain.models import continuation
    now = now or datetime.now(UTC)
    if timing['kind'] == 'daily':
        return continuation('at_time', 'scheduled', due_at=next_daily(timing, now).isoformat())
    if timing['kind'] == 'once':
        return continuation('at_time', 'scheduled', due_at=datetime.fromisoformat(timing['due_at']).astimezone(UTC).isoformat())
    return continuation()
