from datetime import datetime

from pydantic import BaseModel


class CategoryCount(BaseModel):
    category: str
    count: int


class SeverityCount(BaseModel):
    severity: str
    count: int


class RiskTrendPoint(BaseModel):
    label: str
    average_risk: float
    # Present for real recorded snapshots; the client uses it to show when
    # history is still being collected rather than implying a full window.
    captured_at: datetime | None = None


class AnalyticsOverview(BaseModel):
    active_incidents: int
    critical_incidents: int
    average_risk_score: float
    categories: list[CategoryCount]
    severities: list[SeverityCount]
    risk_trend: list[RiskTrendPoint]

