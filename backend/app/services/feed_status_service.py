from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import FeedStatusModel
from app.schemas.feeds import FeedsOverview, FeedStatus

# Display names, in the order the dashboard lists them.
FEEDS: dict[str, str] = {
    "usgs": "USGS earthquakes",
    "gdacs": "GDACS disaster alerts",
    "conflict_news": "Conflict news",
    "reliefweb": "ReliefWeb reports",
}

# The scheduled sync is nominally every 30 minutes but GitHub throttles it to
# roughly every 2-3 hours. Six hours without any attempt means it has stopped,
# not that it is running slowly — the failure that once went unnoticed for a week.
STALE_AFTER = timedelta(hours=6)

# GDELT rate-limits intermittently: in testing it returned 429 to requests
# spaced well beyond its stated "one every 5 seconds" limit, and every call,
# successful or not, took 11-14 s. Retrying would only add that delay again.
# After a failure, skip it for this long and go straight to the Google News
# fallback, then probe it once more.
GDELT_COOLDOWN = timedelta(hours=6)

# Tracked like a feed but not listed on the dashboard: it is the primary source
# behind "conflict_news", recorded separately so the breaker knows when it failed.
GDELT_FEED = "gdelt"


@dataclass
class FeedOutcome:
    feed: str
    items: list = field(default_factory=list)
    error: str | None = None
    note: str | None = None


def disabled_reason(feed: str) -> str | None:
    """Why a feed is switched off by configuration, if it is.

    Computed from settings at read time rather than stored, so setting the
    variable takes effect on the next deploy without touching the database.
    """
    if feed == "reliefweb" and not settings.reliefweb_appname:
        return (
            "Not configured: ReliefWeb only serves approved app names. Request one at "
            "https://apidoc.reliefweb.int/parameters#appname and set RELIEFWEB_APPNAME."
        )
    return None


def short_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:240]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class FeedStatusService:
    def record(self, db: Session, outcomes: list[FeedOutcome]) -> None:
        """Persist one fetch attempt per feed.

        Committed on its own, so the outcome is kept even if persisting the
        fetched incidents fails afterwards.
        """
        now = datetime.now(timezone.utc)
        for outcome in outcomes:
            row = db.get(FeedStatusModel, outcome.feed)
            if row is None:
                row = FeedStatusModel(feed=outcome.feed, consecutive_failures=0, last_item_count=0)
                db.add(row)
            row.last_attempt_at = now
            row.last_item_count = len(outcome.items)
            row.note = outcome.note
            if outcome.error:
                row.last_error = outcome.error
                row.consecutive_failures = (row.consecutive_failures or 0) + 1
            else:
                row.last_error = None
                row.last_success_at = now
                row.consecutive_failures = 0
        db.commit()

    def circuit_open(self, db: Session, feed: str, cooldown: timedelta) -> bool:
        """True when the feed failed on its last attempt and the cooldown since
        then has not elapsed. Skipped runs do not touch the row, so the cooldown
        counts from the real failure."""
        row = db.get(FeedStatusModel, feed)
        if row is None or not row.last_error:
            return False
        return datetime.now(timezone.utc) - _as_utc(row.last_attempt_at) < cooldown

    def overview(self, db: Session) -> FeedsOverview:
        rows = {row.feed: row for row in db.scalars(select(FeedStatusModel)).all()}
        feeds: list[FeedStatus] = []
        for key, label in FEEDS.items():
            row = rows.get(key)
            reason = disabled_reason(key)
            if reason:
                state = "disabled"
            elif row is None:
                state = "unknown"
            elif row.last_error:
                state = "failing"
            elif row.note:
                state = "degraded"
            else:
                state = "healthy"
            feeds.append(
                FeedStatus(
                    feed=key,
                    label=label,
                    state=state,
                    last_attempt_at=_as_utc(row.last_attempt_at) if row else None,
                    last_success_at=_as_utc(row.last_success_at) if row else None,
                    last_error=None if reason else (row.last_error if row else None),
                    note=reason or (row.note if row else None),
                    last_item_count=row.last_item_count if row else 0,
                    consecutive_failures=row.consecutive_failures if row else 0,
                )
            )

        # Disabled feeds are a configuration choice, not a fault: they are listed
        # but left out of the health ratio and the overall state.
        enabled = [f for f in feeds if f.state != "disabled"]
        attempts = [f.last_attempt_at for f in enabled if f.last_attempt_at]
        last_sync = max(attempts) if attempts else None
        healthy = sum(1 for f in enabled if f.state == "healthy")

        if last_sync is None:
            overall = "unknown"
        elif datetime.now(timezone.utc) - last_sync > STALE_AFTER:
            overall = "stalled"
        elif healthy == len(enabled):
            overall = "healthy"
        else:
            overall = "degraded"

        return FeedsOverview(
            overall=overall,
            healthy=healthy,
            total=len(enabled),
            last_sync_at=last_sync,
            feeds=feeds,
        )


feed_status_service = FeedStatusService()
