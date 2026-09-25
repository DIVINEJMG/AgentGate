from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.workforce.drafts import ScheduleDraft

_WEEKDAYS = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}


@dataclass(frozen=True, slots=True)
class CompiledSchedule:
    trigger_config: dict[str, object] | None
    once_at: datetime | None
    human_readable: str
    stop_after_next_run: bool


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown schedule timezone: {value}.") from exc
    return value


def compile_schedule(draft: ScheduleDraft) -> CompiledSchedule:
    timezone = _validate_timezone(draft.timezone or "UTC")
    human = draft.human_readable.strip() or draft.kind.replace("_", " ")

    if draft.kind == "manual":
        return CompiledSchedule(None, None, human, draft.stop_after_next_run)

    if draft.kind == "once":
        if draft.once_at is None:
            raise ValueError("A one-time schedule requires once_at.")
        once_at = draft.once_at
        if once_at.tzinfo is None:
            once_at = once_at.replace(tzinfo=ZoneInfo(timezone))
        return CompiledSchedule(
            None,
            once_at.astimezone(UTC),
            human,
            draft.stop_after_next_run,
        )

    if draft.kind == "event":
        event_key = (draft.event_key or "").strip()
        if not event_key:
            raise ValueError("An event schedule requires event_key.")
        return CompiledSchedule(
            {
                "schedule": {
                    "enabled": False,
                    "cadence": "daily",
                    "timezone": timezone,
                    "localTime": "09:00",
                    "weekdays": [],
                    "intervalMinutes": 60,
                    "missedRunPolicy": "queue_once",
                    "outsideWorkingHoursPolicy": "next_open",
                },
                "apiEnabled": False,
                "internalEventKeys": [event_key],
                "dependencyJobIds": [],
            },
            None,
            human,
            draft.stop_after_next_run,
        )

    local_time = draft.local_time or "09:00"
    weekdays = [value.strip().lower() for value in draft.weekdays if value.strip()]
    invalid_days = set(weekdays) - _WEEKDAYS
    if invalid_days:
        raise ValueError(f"Invalid schedule weekdays: {', '.join(sorted(invalid_days))}.")
    if draft.kind == "weekly" and not weekdays:
        raise ValueError("A weekly schedule requires at least one weekday.")
    interval = draft.interval_minutes or 60
    if draft.kind == "interval" and interval < 60:
        raise ValueError("Autonomous schedules may not run more often than once per hour.")

    schedule = {
        "enabled": True,
        "cadence": (
            "interval"
            if draft.kind == "interval"
            else "weekly"
            if draft.kind == "weekly"
            else "daily"
        ),
        "timezone": timezone,
        "localTime": local_time,
        "weekdays": weekdays,
        "intervalMinutes": interval,
        "missedRunPolicy": "queue_once",
        "outsideWorkingHoursPolicy": "next_open",
    }
    return CompiledSchedule(
        {
            "schedule": schedule,
            "apiEnabled": False,
            "internalEventKeys": [],
            "dependencyJobIds": [],
        },
        None,
        human,
        draft.stop_after_next_run,
    )
