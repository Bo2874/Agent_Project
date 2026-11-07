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


def from_local_input(data):
    payload = data.model_dump(exclude={"at_local"}, mode="json")
    if data.kind == "once":
        if data.at_local is None or data.at_local.utcoffset() is not None:
            raise ValueError("Choose a local date and time in the selected timezone")
        zone = ZoneInfo(data.timezone)
        candidate = data.at_local.replace(tzinfo=zone, fold=0).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) != data.at_local:
            raise ValueError("That local time does not exist during the DST transition")
        payload["at"] = candidate
    elif data.at_local is not None:
        raise ValueError("Recurring schedules do not use a one-time date")
    return ScheduleSpec.model_validate(payload)
