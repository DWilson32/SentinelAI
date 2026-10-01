from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

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


@dataclass
class FeedOutcome:
    feed: str
    items: list = field(default_factory=list)
    error: str | None = None
    note: str | None = None


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

    def overview(self, db: Session) -> FeedsOverview:
        rows = {row.feed: row for row in db.scalars(select(FeedStatusModel)).all()}
        feeds: list[FeedStatus] = []
        for key, label in FEEDS.items():
            row = rows.get(key)
            if row is None:
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
                    last_error=row.last_error if row else None,
                    note=row.note if row else None,
                    last_item_count=row.last_item_count if row else 0,
                    consecutive_failures=row.consecutive_failures if row else 0,
                )
            )

        attempts = [f.last_attempt_at for f in feeds if f.last_attempt_at]
        last_sync = max(attempts) if attempts else None
        healthy = sum(1 for f in feeds if f.state == "healthy")

        if last_sync is None:
            overall = "unknown"
        elif datetime.now(timezone.utc) - last_sync > STALE_AFTER:
            overall = "stalled"
        elif healthy == len(feeds):
            overall = "healthy"
        else:
            overall = "degraded"

        return FeedsOverview(
            overall=overall,
            healthy=healthy,
            total=len(feeds),
            last_sync_at=last_sync,
            feeds=feeds,
        )


feed_status_service = FeedStatusService()
