from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.database import Base


class IncidentModel(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # How the coordinates were found: "reported" by the feed, or read from the
    # headline at "city", "region" or "country" level. None: not located (0, 0).
    geo_precision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Why severity is below what the risk score alone gives, when the evidence
    # caps it (services/credibility.py). None when it is not capped.
    severity_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Combined credibility, independent sources and detected copies (services/credibility.py).
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recommended_actions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    risk_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    risk_drivers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    feature_importance: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)

    sources: Mapped[list["SourceModel"]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    timeline: Mapped[list["TimelineEventModel"]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    agent_runs: Mapped[list["AgentRunModel"]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    reports: Mapped[list["ReportModel"]] = relationship(back_populates="incident", cascade="all, delete-orphan")


class RiskSnapshotModel(Base):
    """Point-in-time record of fleet-wide risk, written after each ingest.

    The dashboard's risk trend reads these rows. Without them there is no
    history to plot, since incidents only carry their own timestamps.
    """

    __tablename__ = "risk_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    active_incidents: Mapped[int] = mapped_column(Integer, nullable=False)
    critical_incidents: Mapped[int] = mapped_column(Integer, nullable=False)
    average_risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    # Scores from different risk models are on different scales; the trend only
    # plots the current model's. None: recorded before models were tracked.
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SourceModel(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = mapped_column(String(128), nullable=False)
    credibility_score: Mapped[float] = mapped_column(Float, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    incident: Mapped[IncidentModel] = relationship(back_populates="sources")


class TimelineEventModel(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    incident: Mapped[IncidentModel] = relationship(back_populates="timeline")


class AgentRunModel(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    incident: Mapped[IncidentModel] = relationship(back_populates="agent_runs")


class ReportModel(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True, nullable=False)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    incident: Mapped[IncidentModel] = relationship(back_populates="reports")


class SourceChunkModel(Base):
    """One embedded chunk of a source document, stored beside the incident it
    belongs to.

    Search joins back to incidents for category, severity and risk score rather
    than copying them in at index time, so a re-scored incident never surfaces
    with stale values the way a denormalised vector payload would.
    """

    __tablename__ = "source_chunks"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Lets a resync skip chunks whose text and embedding model are unchanged.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dimensions), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeedStatusModel(Base):
    """Outcome of the most recent fetch from each public feed.

    Each feed is fetched inside its own try/except so one failure cannot sink an
    ingest — which also meant failures were only ever logged, and a feed could be
    dead for months unnoticed. Recording the outcome makes it visible.
    """

    __tablename__ = "feed_status"

    feed: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the feed succeeded only through a fallback, e.g. GDELT -> Google News.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IncidentAliasModel(Base):
    """An incident id that entity resolution merged into another incident.

    Kept so that links to the old id still lead to the incident it became.
    """

    __tablename__ = "incident_aliases"

    alias_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    merged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
