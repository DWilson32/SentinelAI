from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


ProviderName = Literal["manual", "mock", "gnews", "newsapi"]


class IngestSource(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    url: HttpUrl
    publisher: str = Field(default="Unknown", max_length=128)
    published_at: datetime | None = None
    raw_text: str = Field(min_length=10)
    category: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=255)


class IngestRequest(BaseModel):
    sources: list[IngestSource] = Field(min_length=1, max_length=25)


class ExternalIngestRequest(BaseModel):
    provider: Literal["gnews", "newsapi"] = "gnews"
    query: str = Field(default="flood OR wildfire OR outbreak OR earthquake", min_length=3, max_length=120)
    max_results: int = Field(default=5, ge=1, le=10)


class IngestedIncident(BaseModel):
    incident_id: str
    title: str
    category: str
    severity: str
    risk_score: float
    source_url: str
    created: bool


class IngestResponse(BaseModel):
    provider: ProviderName
    created_count: int
    skipped_count: int
    incidents: list[IngestedIncident]
    message: str

