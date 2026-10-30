from datetime import datetime

from pydantic import BaseModel

from app.schemas.incident import Severity


class Alert(BaseModel):
    id: str
    incident_id: str
    incident_title: str
    incident_location: str
    severity: Severity
    reason: str
    risk_score: float
    credibility: float | None = None
    independent_sources: int | None = None
    created_at: datetime
    acknowledged_at: datetime | None = None
    delivered_at: datetime | None = None
    delivery_error: str | None = None


class AlertsOverview(BaseModel):
    unacknowledged: int
    total: int
    # False when no webhook is configured: alerts are raised and shown, not sent.
    delivery_configured: bool
    alerts: list[Alert]
