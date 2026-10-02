from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import RiskSnapshotModel
from app.ml.risk_model import risk_model
from app.schemas.analytics import AnalyticsOverview, CategoryCount, RiskTrendPoint, SeverityCount
from app.services.incident_service import incident_service

# A snapshot is only written if the newest one is older than this, so a burst of
# ingests does not flood the trend with near-identical points.
SNAPSHOT_MIN_INTERVAL = timedelta(minutes=30)

# How much history the dashboard plots, and how many points it will show.
TREND_WINDOW = timedelta(hours=24)
TREND_MAX_POINTS = 12


class AnalyticsService:
    def get_overview(self, db: Session) -> AnalyticsOverview:
        # The summary row and severity chart describe what is happening now, so
        # they cover active incidents only. The incident list keeps the full record.
        incidents = incident_service.list_incidents(db)
        active = [incident for incident in incidents if incident.active]
        totals = self._totals(incidents, active)

        return AnalyticsOverview(
            active_incidents=totals["active_incidents"],
            tracked_incidents=totals["tracked_incidents"],
            critical_incidents=totals["critical_incidents"],
            average_risk_score=totals["average_risk_score"],
            categories=[
                CategoryCount(category=category, count=count)
                for category, count in Counter(i.category for i in active).most_common()
            ],
            severities=[
                SeverityCount(severity=severity, count=count)
                for severity, count in self._ordered_severities(active)
            ],
            risk_trend=self._risk_trend(db, totals),
        )

    def capture_snapshot(self, db: Session, force: bool = False) -> RiskSnapshotModel | None:
        """Record current fleet risk. Returns None when throttled.

        Called after ingestion rather than on read, so that page views do not
        write rows and the trend reflects data changes rather than traffic.
        """
        now = datetime.now(timezone.utc)

        if not force:
            latest = db.scalar(select(func.max(RiskSnapshotModel.captured_at)))
            if latest is not None:
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
                if now - latest < SNAPSHOT_MIN_INTERVAL:
                    return None

        totals = self._current_totals(db)
        snapshot = RiskSnapshotModel(
            captured_at=now,
            active_incidents=totals["active_incidents"],
            critical_incidents=totals["critical_incidents"],
            average_risk_score=totals["average_risk_score"],
            model_name=risk_model.model_name,
        )
        db.add(snapshot)
        db.commit()
        return snapshot

    def _current_totals(self, db: Session) -> dict:
        incidents = incident_service.list_incidents(db)
        return self._totals(incidents, [incident for incident in incidents if incident.active])

    def _totals(self, incidents: list, active: list) -> dict:
        return {
            "active_incidents": len(active),
            "tracked_incidents": len(incidents),
            # An old critical does not "require immediate investigation".
            "critical_incidents": sum(1 for incident in active if incident.severity == "critical"),
            "average_risk_score": (
                round(sum(incident.risk_score for incident in active) / len(active), 1) if active else 0.0
            ),
        }

    def _ordered_severities(self, incidents: list) -> list[tuple[str, int]]:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        counts = Counter(incident.severity for incident in incidents)
        return sorted(counts.items(), key=lambda item: order.get(item[0], 99))

    def _risk_trend(self, db: Session, totals: dict) -> list[RiskTrendPoint]:
        """Build the trend from stored snapshots only.

        Returns fewer points — possibly just the current one — rather than
        padding with invented history.
        """
        since = datetime.now(timezone.utc) - TREND_WINDOW
        snapshots = list(
            db.scalars(
                select(RiskSnapshotModel)
                .where(RiskSnapshotModel.captured_at >= since)
                # A new risk model starts a new trend: its scores are on another scale.
                .where(RiskSnapshotModel.model_name == risk_model.model_name)
                .order_by(RiskSnapshotModel.captured_at.desc())
                .limit(TREND_MAX_POINTS)
            ).all()
        )
        snapshots.reverse()

        points = [
            RiskTrendPoint(
                label=self._relative_label(snapshot.captured_at),
                average_risk=round(float(snapshot.average_risk_score), 1),
                captured_at=snapshot.captured_at,
            )
            for snapshot in snapshots
        ]

        # End on live numbers so the last point matches the metric cards. Only
        # replace the newest snapshot when it is recent enough to *be* "now";
        # otherwise append, so no real reading is discarded.
        now_point = RiskTrendPoint(
            label="Now",
            average_risk=totals["average_risk_score"],
            captured_at=datetime.now(timezone.utc),
        )
        if snapshots and datetime.now(timezone.utc) - self._as_utc(snapshots[-1].captured_at) < timedelta(minutes=5):
            points[-1] = now_point
        else:
            points.append(now_point)

        return points

    def _as_utc(self, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def _relative_label(self, captured_at: datetime) -> str:
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - captured_at
        hours = delta.total_seconds() / 3600

        if hours < 1:
            minutes = max(1, int(delta.total_seconds() // 60))
            return f"{minutes}m ago"
        return f"{int(round(hours))}h ago"


analytics_service = AnalyticsService()
