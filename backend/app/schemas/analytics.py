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


class AnalyticsOverview(BaseModel):
    active_incidents: int
    critical_incidents: int
    average_risk_score: float
    categories: list[CategoryCount]
    severities: list[SeverityCount]
    risk_trend: list[RiskTrendPoint]

