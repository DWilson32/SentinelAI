from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FeedState = Literal["healthy", "degraded", "failing", "unknown"]
OverallState = Literal["healthy", "degraded", "stalled", "unknown"]


class FeedStatus(BaseModel):
    feed: str
    label: str
    state: FeedState
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    note: str | None
    last_item_count: int
    consecutive_failures: int


class FeedsOverview(BaseModel):
    overall: OverallState
    healthy: int
    total: int
    last_sync_at: datetime | None
    feeds: list[FeedStatus]
