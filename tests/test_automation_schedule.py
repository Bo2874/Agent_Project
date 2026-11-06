from datetime import datetime

import pytest

from backend.schemas.automation import ScheduleSpec


@pytest.mark.parametrize(
    "spec,after,want",
    [
        (
            {"kind": "daily", "timezone": "Asia/Ho_Chi_Minh", "local_time": "08:00"},
            "2026-11-04T02:00:00+00:00",
            "2026-11-05T01:00:00+00:00",
        ),
        (
            {"kind": "weekly", "timezone": "Asia/Ho_Chi_Minh", "local_time": "08:00", "weekday": 0},
            "2026-11-06T00:00:00+00:00",
            "2026-11-09T01:00:00+00:00",
        ),
        (
            {"kind": "daily", "timezone": "America/New_York", "local_time": "02:30"},
            "2026-03-07T08:00:00+00:00",
            "2026-03-09T06:30:00+00:00",
        ),
        (
            {"kind": "daily", "timezone": "America/New_York", "local_time": "01:30"},
            "2026-11-01T05:30:00+00:00",
            "2026-11-02T06:30:00+00:00",
        ),
        (
            {"kind": "daily", "timezone": "America/New_York", "local_time": "01:30"},
            "2026-11-01T04:00:00+00:00",
            "2026-11-01T05:30:00+00:00",
        ),
        (
            {"kind": "once", "timezone": "UTC", "at": "2026-11-07T10:00:00+00:00"},
            "2026-11-07T09:00:00+00:00",
            "2026-11-07T10:00:00+00:00",
        ),
        (
            {"kind": "once", "timezone": "UTC", "at": "2026-11-07T10:00:00+00:00"},
            "2026-11-07T10:00:00+00:00",
            None,
        ),
    ],
)
def test_next_occurrence_is_strictly_future_and_dst_safe(spec, after, want):
    from backend.services.automations.schedules import next_occurrence

    result = next_occurrence(ScheduleSpec.model_validate(spec), after=datetime.fromisoformat(after))
    assert (result.isoformat() if result else None) == want


def test_naive_clock_is_rejected():
    from backend.services.automations.schedules import next_occurrence

    with pytest.raises(ValueError):
        next_occurrence(
            ScheduleSpec(kind="daily", timezone="UTC", local_time="08:00"),
            after=datetime(2026, 11, 7),
        )


def test_empty_optional_scheduler_health_path_does_not_target_working_directory():
    from backend.core.config import Settings

    settings = Settings(
        _env_file=None,
        db_password="fixture",
        auth_secret_key="x" * 32,
        automation_scheduler_heartbeat_path="",
    )
    assert settings.automation_scheduler_heartbeat_path is None
