from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]
IncidentStatus = Literal["monitoring", "investigating", "contained", "escalated"]


class Source(BaseModel):
    id: str
    title: str
    url: str
    publisher: str
    credibility_score: float = Field(ge=0, le=1)
    published_at: datetime
    raw_text: str


class Incident(BaseModel):
    id: str
    title: str
    category: str
    location: str
    latitude: float
    longitude: float
    severity: Severity
    risk_score: float = Field(ge=0, le=100)
    status: IncidentStatus
    summary: str
    created_at: datetime
    updated_at: datetime
    # Newest source publication time; decides whether the incident is active.
    last_activity_at: datetime | None = None
    active: bool = True
    # Reports grouped into this incident by entity resolution.
    source_count: int = 1
    # "reported" by the feed; "city", "region" or "country" when read from the
    # headline; None when not located.
    geo_precision: str | None = None


class TimelineEvent(BaseModel):
    timestamp: datetime
    label: str
    description: str


class RiskExplanation(BaseModel):
    confidence: float = Field(ge=0, le=1)
    drivers: list[str]
    feature_importance: dict[str, float]


class IncidentDetail(Incident):
    sources: list[Source]
    timeline: list[TimelineEvent]
    recommended_actions: list[str]
    risk_explanation: RiskExplanation

