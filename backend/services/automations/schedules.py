"""Calendar schedules, UTC instants, and an explicit DST fold/gap policy."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.schemas.automation import ScheduleSpec


def next_occurrence(schedule: ScheduleSpec, *, after: datetime) -> datetime | None:
    if after.utcoffset() is None:
        raise ValueError("Scheduler clock must be timezone aware")
    after = after.astimezone(UTC)
    if schedule.kind == "once":
        at = schedule.at.astimezone(UTC)
        return at if at > after else None
    zone = ZoneInfo(schedule.timezone)
    day = after.astimezone(zone).date()
    for offset in range(16):
        candidate_day = day + timedelta(days=offset)
        if schedule.kind == "weekly" and candidate_day.weekday() != schedule.weekday:
            continue
        naive = datetime.combine(candidate_day, schedule.local_time)
        candidate = naive.replace(tzinfo=zone, fold=0).astimezone(UTC)
        # Round-trip rejects imaginary wall times during a forward transition.
        # fold=0 is the sole occurrence admitted during a backward transition.
        if candidate.astimezone(zone).replace(tzinfo=None) == naive and candidate > after:
            return candidate
    raise ValueError("No valid occurrence in the next two weekly intervals")


def missed_occurrences(schedule: ScheduleSpec, first: datetime, now: datetime) -> int:
    count, at = 0, first
    while at is not None and at <= now:
        count += 1
        at = next_occurrence(schedule, after=at)
    return count
