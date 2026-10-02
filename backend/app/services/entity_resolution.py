"""Entity resolution: decide whether a report is about an incident that is
already tracked, so one event becomes one incident with several sources rather
than several incidents with one source each.

The rules were set against the live data on 2 Oct 2026, when every one of the
146 incidents had exactly one source:

* Structured feeds (USGS, GDACS) are never matched on text. Their reports are
  generated from templates, so different events read almost alike: three
  different cyclones scored 0.93 cosine similarity, and two earthquakes 7,850 km
  apart 0.94. GDACS cyclones and floods carry an event id in their URL, which
  the URL check already deduplicates.
* Earthquakes match on physical identity: epicentres within 100 km, magnitudes
  within 0.5, origin times within 5 minutes. GDACS reuses the USGS epicentre
  (0 km apart in every live pair) but stamps its report 15-60 minutes after the
  quake, so its origin time is read from its title. A looser time window would
  merge real aftershocks, such as two M4.8 quakes 17 km and 2.4 hours apart.
* News matches when a news report on an incident in the same category,
  published within 48 hours, has cosine similarity of at least 0.92. Every
  English pair above that in the live data was the same story, though not
  always the same event: two Israeli strikes in Gaza a day apart scored 0.94.
  Below about 0.90 different stories start to pair up (a Sudan ceasefire plan
  and Iran's ceasefire talks scored 0.875).
* Text that is mostly not in Latin script matches only on an identical
  headline. The embedding model is English-only, and unrelated Russian and
  Ukrainian articles scored up to 0.94 against each other.
"""

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    AgentRunModel,
    IncidentAliasModel,
    IncidentModel,
    ReportModel,
    SourceChunkModel,
    SourceModel,
    TimelineEventModel,
)
from app.services.credibility import STRUCTURED_HOSTS, apply_evidence, is_structured
from app.services.headlines import normalized_title
from app.services.lifecycle import as_utc
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)

QUAKE_MAX_DISTANCE_KM = 100.0
QUAKE_MAX_MAGNITUDE_GAP = 0.5
QUAKE_MAX_ORIGIN_GAP = timedelta(minutes=5)
# GDACS publishes up to an hour after the quake, so candidates are fetched by
# publication time over a wider window and then compared on origin time.
QUAKE_CANDIDATE_WINDOW = timedelta(hours=3)

NEWS_WINDOW = timedelta(hours=48)
NEWS_MIN_SIMILARITY = 0.92

_USGS_MAGNITUDE = re.compile(r"^M\s*(\d+(?:\.\d+)?)\b")
_GDACS_MAGNITUDE = re.compile(r"Magnitude\s+(\d+(?:\.\d+)?)\s*M\b", re.IGNORECASE)
_GDACS_ORIGIN = re.compile(r"\b(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}) UTC\b")


@dataclass(frozen=True)
class Report:
    """The parts of a source that resolution compares."""

    url: str
    title: str
    category: str
    published_at: datetime
    latitude: float | None = None
    longitude: float | None = None

    @property
    def structured(self) -> bool:
        return is_structured(self.url)


@dataclass(frozen=True)
class Match:
    incident_id: str
    reason: str


@dataclass(frozen=True)
class Pending:
    """A report stored earlier in the same ingest, as a new incident or added to
    one. It is not in the index until the ingest finishes, so it is compared in
    memory."""

    incident_id: str
    report: Report
    embedding: list[float] | None


@dataclass
class MergeGroup:
    survivor_id: str
    # Incidents to fold into the survivor, each with why it matched.
    members: list[tuple[str, str]] = field(default_factory=list)


def magnitude(title: str) -> float | None:
    found = _USGS_MAGNITUDE.search(title) or _GDACS_MAGNITUDE.search(title)
    return float(found.group(1)) if found else None


def origin_time(report: Report) -> datetime:
    """When the event happened. USGS publishes at the origin time; GDACS
    publishes later but states the origin time in its title."""
    if (urlparse(report.url).hostname or "").endswith("gdacs.org"):
        found = _GDACS_ORIGIN.search(report.title)
        if found:
            day, month, year, hour, minute = map(int, found.groups())
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    return as_utc(report.published_at)


def mostly_latin(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return True
    latin = sum(1 for ch in letters if unicodedata.name(ch, "").startswith("LATIN"))
    return latin / len(letters) >= 0.8


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _located(report: Report) -> bool:
    # News without a geocoded place is stored at (0, 0).
    return (
        report.latitude is not None
        and report.longitude is not None
        and not (report.latitude == 0 and report.longitude == 0)
    )


def same_event(a: Report, b: Report, similarity: float | None = None) -> str | None:
    """Why a and b describe the same event, or None if they do not.

    similarity is the cosine similarity of the two reports' embeddings, when
    both have one. The single decision used both at ingest and for backfill.
    """
    if a.category != b.category:
        return None
    if a.structured or b.structured:
        if a.structured and b.structured and a.category == "Earthquake":
            return _same_earthquake(a, b)
        return None
    if abs(as_utc(a.published_at) - as_utc(b.published_at)) > NEWS_WINDOW:
        return None
    headline = normalized_title(a.title)
    if headline and headline == normalized_title(b.title):
        return "same headline"
    if (
        similarity is not None
        and similarity >= NEWS_MIN_SIMILARITY
        and mostly_latin(a.title)
        and mostly_latin(b.title)
    ):
        return f"similar report, {similarity:.2f} similarity"
    return None


def _same_earthquake(a: Report, b: Report) -> str | None:
    mag_a, mag_b = magnitude(a.title), magnitude(b.title)
    if mag_a is None or mag_b is None or abs(mag_a - mag_b) > QUAKE_MAX_MAGNITUDE_GAP:
        return None
    if not (_located(a) and _located(b)):
        return None
    gap = abs(origin_time(a) - origin_time(b))
    if gap > QUAKE_MAX_ORIGIN_GAP:
        return None
    km = distance_km(a.latitude, a.longitude, b.latitude, b.longitude)
    if km > QUAKE_MAX_DISTANCE_KM:
        return None
    return f"same earthquake, M{mag_a:g} and M{mag_b:g}, {km:.0f} km and {int(gap.total_seconds() // 60)} min apart"


class EntityResolver:
    def find_match(
        self, db: Session, report: Report, embedding: list[float] | None, pending: list[Pending]
    ) -> Match | None:
        """The tracked incident this report is about, if any."""
        if report.structured:
            if report.category != "Earthquake" or not _located(report):
                return None
            return self._match_earthquake(db, report)
        return self._match_news(db, report, embedding, pending)

    def _match_earthquake(self, db: Session, report: Report) -> Match | None:
        origin = origin_time(report)
        rows = db.execute(
            select(
                SourceModel.incident_id,
                SourceModel.url,
                SourceModel.title,
                SourceModel.published_at,
                IncidentModel.latitude,
                IncidentModel.longitude,
            )
            .join(IncidentModel, IncidentModel.id == SourceModel.incident_id)
            .where(IncidentModel.category == "Earthquake")
            .where(SourceModel.published_at.between(origin - QUAKE_CANDIDATE_WINDOW, origin + QUAKE_CANDIDATE_WINDOW))
        ).all()
        for row in rows:
            candidate = Report(
                url=row.url,
                title=row.title,
                category="Earthquake",
                published_at=row.published_at,
                latitude=row.latitude,
                longitude=row.longitude,
            )
            reason = same_event(report, candidate)
            if reason:
                return Match(row.incident_id, reason)
        return None

    def _match_news(
        self, db: Session, report: Report, embedding: list[float] | None, pending: list[Pending]
    ) -> Match | None:
        since = report.published_at - NEWS_WINDOW
        until = report.published_at + NEWS_WINDOW

        # Identical headlines first: cheap, and the only rule for non-Latin text.
        rows = db.execute(
            select(SourceModel.incident_id, SourceModel.url, SourceModel.title, SourceModel.published_at)
            .join(IncidentModel, IncidentModel.id == SourceModel.incident_id)
            .where(IncidentModel.category == report.category)
            .where(SourceModel.published_at.between(since, until))
        ).all()
        for row in rows:
            candidate = Report(url=row.url, title=row.title, category=report.category, published_at=row.published_at)
            if not candidate.structured and normalized_title(candidate.title) == normalized_title(report.title):
                return Match(row.incident_id, "same headline")

        if embedding is None or not mostly_latin(report.title):
            return None

        scored: list[tuple[float, str, Report]] = []
        for item in pending:
            if item.embedding is not None:
                scored.append((cosine(embedding, item.embedding), item.incident_id, item.report))
        if vector_store.available:
            try:
                # A savepoint, so a failed lookup cannot abort the ingest transaction.
                with db.begin_nested():
                    hits = vector_store.nearest_reports(
                        db,
                        embedding,
                        category=report.category,
                        published_from=since,
                        published_to=until,
                        exclude_url_hosts=STRUCTURED_HOSTS,
                    )
            except Exception as exc:  # noqa: BLE001 - fall back to no semantic match
                logger.warning("Semantic match lookup failed: %s", exc)
                hits = []
            for hit in hits:
                candidate = Report(
                    url=hit.url, title=hit.title, category=report.category, published_at=hit.published_at
                )
                scored.append((hit.similarity, hit.incident_id, candidate))

        for similarity, incident_id, candidate in sorted(scored, key=lambda item: item[0], reverse=True):
            reason = same_event(report, candidate, similarity)
            if reason:
                return Match(incident_id, reason)
            if similarity < NEWS_MIN_SIMILARITY:
                break
        return None

    def plan_merges(self, db: Session) -> list[MergeGroup]:
        """Group existing incidents that the rules say are one event.

        Built for the one-off backfill of incidents ingested before entity
        resolution existed; new reports are resolved as they arrive.
        """
        incidents = db.scalars(select(IncidentModel).options(selectinload(IncidentModel.sources))).all()
        vectors = vector_store.first_chunk_vectors(db) if vector_store.available else {}

        reports = {
            incident.id: [
                (
                    Report(
                        url=source.url,
                        title=source.title,
                        category=incident.category,
                        published_at=source.published_at,
                        latitude=incident.latitude,
                        longitude=incident.longitude,
                    ),
                    vectors.get(source.id),
                )
                for source in incident.sources
            ]
            for incident in incidents
        }

        parent = {incident.id: incident.id for incident in incidents}

        def root(incident_id: str) -> str:
            while parent[incident_id] != incident_id:
                parent[incident_id] = parent[parent[incident_id]]
                incident_id = parent[incident_id]
            return incident_id

        by_category: dict[str, list[str]] = {}
        for incident in incidents:
            by_category.setdefault(incident.category, []).append(incident.id)

        reasons: dict[str, str] = {}
        for ids in by_category.values():
            for i, first in enumerate(ids):
                for second in ids[i + 1 :]:
                    reason = self._incidents_match(reports[first], reports[second])
                    if reason:
                        reasons.setdefault(second, reason)
                        reasons.setdefault(first, reason)
                        parent[root(second)] = root(first)

        clusters: dict[str, list[IncidentModel]] = {}
        for incident in incidents:
            clusters.setdefault(root(incident.id), []).append(incident)

        groups: list[MergeGroup] = []
        for members in clusters.values():
            if len(members) < 2:
                continue
            # The first report survives, so the earliest link to the event keeps working.
            members.sort(key=lambda item: (self._first_published(item), as_utc(item.created_at), item.id))
            survivor, rest = members[0], members[1:]
            groups.append(MergeGroup(survivor.id, [(item.id, reasons[item.id]) for item in rest]))
        return groups

    def _incidents_match(self, first: list[tuple[Report, list[float] | None]], second: list[tuple[Report, list[float] | None]]) -> str | None:
        for report_a, vector_a in first:
            for report_b, vector_b in second:
                similarity = None
                if vector_a is not None and vector_b is not None and not (report_a.structured or report_b.structured):
                    similarity = cosine(vector_a, vector_b)
                reason = same_event(report_a, report_b, similarity)
                if reason:
                    return reason
        return None

    def _first_published(self, incident: IncidentModel) -> datetime:
        return min((as_utc(source.published_at) for source in incident.sources), default=as_utc(incident.created_at))

    def apply_merges(self, db: Session, groups: list[MergeGroup]) -> None:
        """Fold each group into its survivor, in one transaction.

        Sources, timeline, agent runs, reports and index chunks move to the
        survivor; nothing is discarded except the emptied incident rows, whose
        ids become aliases of the survivor.
        """
        now = datetime.now(timezone.utc)
        for group in groups:
            survivor = db.get(IncidentModel, group.survivor_id)
            merged = [db.get(IncidentModel, incident_id) for incident_id, _ in group.members]

            # An incident is as severe as its most severe report; severity
            # itself is set below, from the merged evidence.
            strongest = max([survivor, *merged], key=lambda item: item.risk_score)
            if strongest is not survivor:
                for column in ("risk_score", "risk_confidence", "risk_drivers", "feature_importance"):
                    setattr(survivor, column, getattr(strongest, column))
            survivor.updated_at = now

            for incident, (incident_id, reason) in zip(merged, group.members, strict=True):
                title = incident.title
                db.expunge(incident)
                for model in (SourceModel, TimelineEventModel, AgentRunModel, ReportModel, SourceChunkModel, IncidentAliasModel):
                    db.execute(
                        update(model)
                        .where(model.incident_id == incident_id)
                        .values(incident_id=survivor.id)
                        .execution_options(synchronize_session=False)
                    )
                db.execute(delete(IncidentModel).where(IncidentModel.id == incident_id).execution_options(synchronize_session=False))
                db.add(IncidentAliasModel(alias_id=incident_id, incident_id=survivor.id, merged_at=now))
                db.add(
                    TimelineEventModel(
                        incident_id=survivor.id,
                        timestamp=now,
                        label="Merged",
                        description=f"Merged a duplicate incident ({reason}): {title}",
                    )
                )
            # The sources moved in bulk, so reload them before weighing the evidence.
            db.expire(survivor, ["sources"])
            apply_evidence(survivor)
        db.commit()


entity_resolver = EntityResolver()
