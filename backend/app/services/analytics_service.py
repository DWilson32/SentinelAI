from collections import Counter

from sqlalchemy.orm import Session

from app.schemas.analytics import AnalyticsOverview, CategoryCount, RiskTrendPoint, SeverityCount
from app.services.incident_service import incident_service


class AnalyticsService:
    def get_overview(self, db: Session) -> AnalyticsOverview:
        incidents = incident_service.list_incidents(db)
        categories = Counter(incident.category for incident in incidents)
        severities = Counter(incident.severity for incident in incidents)
        average_risk = round(sum(incident.risk_score for incident in incidents) / len(incidents), 1) if incidents else 0

        return AnalyticsOverview(
            active_incidents=len(incidents),
            critical_incidents=severities.get("critical", 0),
            average_risk_score=average_risk,
            categories=[CategoryCount(category=category, count=count) for category, count in categories.items()],
            severities=[SeverityCount(severity=severity, count=count) for severity, count in severities.items()],
            risk_trend=[
                RiskTrendPoint(label="24h ago", average_risk=58),
                RiskTrendPoint(label="12h ago", average_risk=67),
                RiskTrendPoint(label="Now", average_risk=average_risk),
            ],
        )


analytics_service = AnalyticsService()
