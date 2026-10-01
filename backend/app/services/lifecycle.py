"""When an incident counts as active.

"Active Incidents" used to count every row ever ingested, so a week-old quake
stayed active forever. An incident is now active while it has been reported
recently, measured from its newest source.

One window does not fit every crisis type. Earthquakes are point events whose
response and aftershock risk concentrate in the first few days; floods persist,
and GDACS routinely reports flood events lasting a week or more. Windows were
set against the live data in Oct 2026, where the oldest flood still being
reported was 11 days old.
"""

from datetime import datetime, timedelta, timezone

DEFAULT_WINDOW = timedelta(hours=72)
CATEGORY_WINDOWS: dict[str, timedelta] = {
    "Flood": timedelta(days=7),
}


def window_for(category: str) -> timedelta:
    return CATEGORY_WINDOWS.get(category, DEFAULT_WINDOW)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def is_active(category: str, last_activity_at: datetime | None, now: datetime | None = None) -> bool:
    if last_activity_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now - as_utc(last_activity_at) <= window_for(category)
