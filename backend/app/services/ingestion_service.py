from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
import logging
import xml.etree.ElementTree as ET
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IncidentModel, SourceModel, TimelineEventModel
from app.ml.risk_model import risk_model
from app.schemas.ingestion import ExternalIngestRequest, IngestRequest, IngestResponse, IngestSource, IngestedIncident
from app.schemas.risk import RiskPrediction, RiskPredictionRequest
from app.services.embedding_service import embedding_service
from app.services.entity_resolution import Pending, Report, entity_resolver, mostly_latin
from app.services.feed_status_service import (
    FEEDS,
    GDELT_COOLDOWN,
    GDELT_FEED,
    FeedOutcome,
    disabled_reason,
    feed_status_service,
    short_error,
)
from app.services.lifecycle import as_utc
from app.services.rag_index_service import document_text, rag_index_service
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Prepared:
    """A new source with everything derived from it before it is stored."""

    source: IngestSource
    url: str
    published_at: datetime
    category: str
    location: str
    summary: str
    credibility: float
    prediction: RiskPrediction
    report: Report


class IngestionService:
    async def ingest_manual(self, db: Session, request: IngestRequest) -> IngestResponse:
        return self._persist_sources(db, "manual", request.sources)

    async def ingest_mock(self, db: Session) -> IngestResponse:
        now = datetime.now(timezone.utc)
        sources = [
            IngestSource(
                title="Cyclone rainfall triggers flash flood warnings in coastal Odisha",
                url="https://example.com/mock-odisha-flood-warning",
                publisher="Mock Disaster Wire",
                published_at=now - timedelta(minutes=35),
                raw_text=(
                    "Emergency officials issued flash flood warnings after cyclone-linked rainfall intensified "
                    "near low-lying coastal districts. Evacuation teams are preparing shelters."
                ),
                category="Flood",
                location="Odisha, India",
            ),
            IngestSource(
                title="New ransomware campaign targets regional hospital systems",
                url="https://example.com/mock-hospital-ransomware",
                publisher="Mock Cyber Watch",
                published_at=now - timedelta(minutes=58),
                raw_text=(
                    "Multiple regional hospitals reported system outages after a suspected ransomware campaign. "
                    "Patient scheduling and lab systems are affected while incident response teams investigate."
                ),
                category="Cybersecurity",
                location="United States",
            ),
        ]
        return self._persist_sources(db, "mock", sources)

    async def ingest_external(self, db: Session, request: ExternalIngestRequest) -> IngestResponse:
        if request.provider == "gnews":
            sources = await self._fetch_gnews(request.query, request.max_results)
        else:
            sources = await self._fetch_newsapi(request.query, request.max_results)
        return self._persist_sources(db, request.provider, sources)

    async def ingest_public_feeds(self, db: Session, max_results: int = 18) -> IngestResponse:
        skip_gdelt = feed_status_service.circuit_open(db, GDELT_FEED, GDELT_COOLDOWN)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            outcomes = await self._fetch_public_sources(client, max_results, skip_gdelt=skip_gdelt)
        feed_status_service.record(db, outcomes)
        # Only listed feeds contribute items. The GDELT tracking outcome carries the
        # same items as conflict_news when it succeeds, and must not double them.
        sources = [item for outcome in outcomes if outcome.feed in FEEDS for item in outcome.items]
        return self._persist_sources(db, "public", sources[:max_results])

    async def _fetch_public_sources(
        self, client: httpx.AsyncClient, max_results: int, *, skip_gdelt: bool = False
    ) -> list[FeedOutcome]:
        # Each feed fails independently so one outage cannot sink an ingest. The
        # outcome is returned rather than only logged, so a dead feed is visible.
        async def run(feed: str, fetch) -> FeedOutcome:
            try:
                return FeedOutcome(feed=feed, items=await fetch())
            except Exception as exc:
                logger.warning("Public feed %s failed: %s", feed, exc)
                return FeedOutcome(feed=feed, error=short_error(exc))

        outcomes = [
            await run("usgs", lambda: self._fetch_usgs_earthquakes(client, max_results=max(3, max_results // 3))),
            await run("gdacs", lambda: self._fetch_gdacs_events(client, max_results=max(3, max_results // 4))),
        ]

        # Also yields GDELT's own outcome, which the circuit breaker reads.
        outcomes += await self._fetch_conflict_news(client, max_results=max(4, max_results // 3), skip_gdelt=skip_gdelt)

        # Skipped entirely when unconfigured: the dashboard shows it as disabled
        # with instructions, instead of a request that is certain to 403.
        if not disabled_reason("reliefweb"):
            outcomes.append(
                await run("reliefweb", lambda: self._fetch_reliefweb_reports(client, max_results=max(2, max_results // 6)))
            )
        return outcomes

    def _persist_sources(self, db: Session, provider: str, sources: list[IngestSource]) -> IngestResponse:
        incidents: list[IngestedIncident] = []
        skipped = 0
        fresh: list[IngestSource] = []
        seen_urls: set[str] = set()

        for source in sources:
            source_url = str(source.url)
            existing_source = db.scalar(select(SourceModel).where(SourceModel.url == source_url))
            if existing_source is not None:
                existing_incident = db.get(IncidentModel, existing_source.incident_id)
                if existing_incident is not None:
                    incidents.append(self._ingested(existing_incident, source_url, created=False))
                skipped += 1
                continue
            if source_url in seen_urls:
                skipped += 1
                continue
            seen_urls.add(source_url)
            fresh.append(source)

        prepared = [self._prepare(provider, source) for source in fresh]
        embeddings = self._match_embeddings(prepared)
        pending: list[Pending] = []
        touched: list[str] = []

        for item, embedding in zip(prepared, embeddings, strict=True):
            # One event is one incident: a report about something already
            # tracked becomes another source of it instead of a new incident.
            match = entity_resolver.find_match(db, item.report, embedding, pending)
            if match is not None:
                incident = db.get(IncidentModel, match.incident_id)
                self._add_report(incident, item, match.reason)
                incidents.append(self._ingested(incident, item.url, created=False, matched=True))
            else:
                incident = self._new_incident(item, provider)
                db.add(incident)
                incidents.append(self._ingested(incident, item.url, created=True))
            # Later reports in this batch are matched against what was just stored.
            db.flush()
            pending.append(Pending(incident.id, item.report, embedding))
            if incident.id not in touched:
                touched.append(incident.id)

        db.commit()
        if settings.index_ingested_sources:
            try:
                rag_index_service.index_incidents(db, touched)
            except Exception as exc:
                logger.warning("Incident ingestion succeeded, but RAG indexing failed: %s", exc)
                # Otherwise the aborted transaction makes the snapshot below fail too.
                db.rollback()
        created_count = sum(1 for incident in incidents if incident.created)
        matched_count = sum(1 for incident in incidents if incident.matched)

        # Record fleet risk after the data changed, so the dashboard trend is
        # built from real readings instead of placeholders.
        try:
            from app.services.analytics_service import analytics_service

            analytics_service.capture_snapshot(db)
        except Exception as exc:
            logger.warning("Risk snapshot capture failed after ingestion: %s", exc)

        return IngestResponse(
            provider=provider,
            created_count=created_count,
            matched_count=matched_count,
            skipped_count=skipped,
            incidents=incidents,
            message=(
                f"Ingested {created_count} new incident(s); added {matched_count} report(s) to "
                f"existing incidents; skipped {skipped} duplicate source(s)."
            ),
        )

    def _prepare(self, provider: str, source: IngestSource) -> "_Prepared":
        published_at = as_utc(source.published_at) if source.published_at else datetime.now(timezone.utc)
        category = source.category or self._infer_category(source.title, source.raw_text)
        credibility = self._publisher_credibility(provider)
        prediction = risk_model.predict(
            RiskPredictionRequest(
                title=source.title,
                text=source.raw_text,
                category=category,
                source_credibility=credibility,
                source_count=1,
            )
        )
        return _Prepared(
            source=source,
            url=str(source.url),
            published_at=published_at,
            category=category,
            location=source.location or self._infer_location(source.raw_text),
            summary=self._summarize(source.raw_text),
            credibility=credibility,
            prediction=prediction,
            report=Report(
                url=str(source.url),
                title=source.title,
                category=category,
                published_at=published_at,
                latitude=source.latitude,
                longitude=source.longitude,
            ),
        )

    def _match_embeddings(self, prepared: list["_Prepared"]) -> list[list[float] | None]:
        """Embeddings of news reports, for matching them to tracked incidents.

        Built from the same text as each report's first index chunk, so a new
        report compares like-for-like with stored ones. Skipped where they could
        not be searched (no pgvector), and for structured feeds and non-Latin
        text, which never match on text.
        """
        result: list[list[float] | None] = [None] * len(prepared)
        if not vector_store.available:
            return result
        wanted = [
            index
            for index, item in enumerate(prepared)
            if not item.report.structured and mostly_latin(item.report.title)
        ]
        if not wanted:
            return result
        texts = [
            rag_index_service.first_chunk(
                document_text(
                    title=prepared[index].source.title,
                    category=prepared[index].category,
                    location=prepared[index].location,
                    summary=prepared[index].summary,
                    source_title=prepared[index].source.title,
                    publisher=prepared[index].source.publisher,
                    content=prepared[index].source.raw_text,
                )
            )
            for index in wanted
        ]
        try:
            vectors = embedding_service.embed(texts)
        except Exception as exc:
            logger.warning("Embedding reports for matching failed; matching on headlines only: %s", exc)
            return result
        for index, vector in zip(wanted, vectors, strict=True):
            result[index] = vector
        return result

    def _new_incident(self, item: "_Prepared", provider: str) -> IncidentModel:
        now = datetime.now(timezone.utc)
        incident = IncidentModel(
            id=f"inc-{uuid4().hex[:12]}",
            title=item.source.title,
            category=item.category,
            location=item.location,
            latitude=item.source.latitude or 0.0,
            longitude=item.source.longitude or 0.0,
            summary=item.summary,
            created_at=now,
            updated_at=now,
        )
        self._apply_prediction(incident, item.prediction)
        incident.sources = [self._source_row(item)]
        incident.timeline = [
            TimelineEventModel(
                timestamp=now,
                label="Ingested",
                description=f"Incident created from {provider} source: {item.source.publisher}.",
            )
        ]
        return incident

    def _add_report(self, incident: IncidentModel, item: "_Prepared", reason: str) -> None:
        now = datetime.now(timezone.utc)
        incident.sources.append(self._source_row(item))
        incident.timeline.append(
            TimelineEventModel(
                timestamp=now,
                label="Report added",
                description=f"{item.source.publisher}: {item.source.title} (matched as {reason}).",
            )
        )
        incident.updated_at = now
        # An incident is as severe as its most severe report.
        if item.prediction.risk_score > incident.risk_score:
            self._apply_prediction(incident, item.prediction)

    def _apply_prediction(self, incident: IncidentModel, prediction: RiskPrediction) -> None:
        incident.risk_score = prediction.risk_score
        incident.severity = prediction.severity
        incident.status = "investigating" if prediction.severity in {"high", "critical"} else "monitoring"
        incident.recommended_actions = self._recommended_actions(incident.category, prediction.severity)
        incident.risk_confidence = prediction.confidence
        incident.risk_drivers = prediction.drivers
        incident.feature_importance = prediction.feature_importance

    def _source_row(self, item: "_Prepared") -> SourceModel:
        return SourceModel(
            id=f"src-{uuid4().hex[:12]}",
            title=item.source.title,
            url=item.url,
            publisher=item.source.publisher,
            credibility_score=item.credibility,
            published_at=item.published_at,
            raw_text=item.source.raw_text,
        )

    def _ingested(
        self, incident: IncidentModel, source_url: str, *, created: bool, matched: bool = False
    ) -> IngestedIncident:
        return IngestedIncident(
            incident_id=incident.id,
            title=incident.title,
            category=incident.category,
            severity=incident.severity,
            risk_score=incident.risk_score,
            source_url=source_url,
            created=created,
            matched=matched,
        )

    async def _fetch_gnews(self, query: str, max_results: int) -> list[IngestSource]:
        if not settings.gnews_api_key:
            raise ValueError("GNEWS_API_KEY is not configured")
        params = {
            "q": query,
            "max": max_results,
            "lang": "en",
            "apikey": settings.gnews_api_key,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get("https://gnews.io/api/v4/search", params=params)
            response.raise_for_status()
        articles = response.json().get("articles", [])
        return [
            IngestSource(
                title=article.get("title") or "Untitled crisis report",
                url=article.get("url"),
                publisher=(article.get("source") or {}).get("name") or "GNews",
                published_at=self._parse_datetime(article.get("publishedAt")),
                raw_text=" ".join(part for part in [article.get("description"), article.get("content")] if part),
            )
            for article in articles
            if article.get("url") and (article.get("description") or article.get("content"))
        ]

    async def _fetch_usgs_earthquakes(self, client: httpx.AsyncClient, max_results: int) -> list[IngestSource]:
        response = await client.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson")
        response.raise_for_status()
        features = response.json().get("features", [])
        sources: list[IngestSource] = []
        for feature in features[:max_results]:
            properties = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates") or []
            longitude = float(coordinates[0]) if len(coordinates) >= 2 and coordinates[0] is not None else None
            latitude = float(coordinates[1]) if len(coordinates) >= 2 and coordinates[1] is not None else None
            magnitude = properties.get("mag")
            place = properties.get("place") or "Unknown location"
            event_time = self._datetime_from_millis(properties.get("time"))
            title = properties.get("title") or f"Magnitude {magnitude} earthquake - {place}"
            raw_text = (
                f"{title}. USGS reported a magnitude {magnitude} earthquake near {place}. "
                f"Alert level: {properties.get('alert') or 'not assigned'}. "
                f"Tsunami flag: {properties.get('tsunami', 0)}. "
                f"Significance score: {properties.get('sig', 'unknown')}."
            )
            url = properties.get("url")
            if not url:
                continue
            sources.append(
                IngestSource(
                    title=title,
                    url=url,
                    publisher="USGS Earthquake Hazards Program",
                    published_at=event_time,
                    raw_text=raw_text,
                    category="Earthquake",
                    location=place,
                    latitude=latitude,
                    longitude=longitude,
                )
            )
        return sources

    async def _fetch_reliefweb_reports(self, client: httpx.AsyncClient, max_results: int) -> list[IngestSource]:
        payload = {
            "limit": max_results,
            "sort": ["date.created:desc"],
            "query": {
                "value": "flood OR earthquake OR wildfire OR cyclone OR outbreak OR emergency OR disaster"
            },
            "fields": {
                "include": [
                    "title",
                    "url",
                    "body",
                    "date.created",
                    "source.name",
                    "country.name",
                    "primary_country.name",
                    "disaster_type.name",
                ]
            },
        }
        response = await client.post(
            "https://api.reliefweb.int/v2/reports",
            params={"appname": settings.reliefweb_appname},
            headers={"User-Agent": "SentinelAI local development"},
            json=payload,
        )
        response.raise_for_status()
        reports = response.json().get("data", [])
        sources: list[IngestSource] = []
        for report in reports:
            fields = report.get("fields") or {}
            title = fields.get("title") or "ReliefWeb crisis update"
            body = self._plain_text(fields.get("body") or "")
            url = fields.get("url") or f"https://reliefweb.int/report/{report.get('id')}"
            country = self._first_name(fields.get("primary_country")) or self._first_name(fields.get("country"))
            disaster_type = self._first_name(fields.get("disaster_type"))
            publisher = self._first_name(fields.get("source")) or "ReliefWeb"
            raw_text = " ".join(
                part
                for part in [
                    f"{title}.",
                    f"Disaster type: {disaster_type}." if disaster_type else "",
                    body[:900],
                ]
                if part
            )
            sources.append(
                IngestSource(
                    title=title,
                    url=url,
                    publisher=publisher,
                    published_at=self._parse_datetime((fields.get("date") or {}).get("created")),
                    raw_text=raw_text,
                    category=self._infer_category(title, f"{disaster_type or ''} {body}"),
                    location=country or "Global",
                )
            )
        return sources

    async def _fetch_gdacs_events(self, client: httpx.AsyncClient, max_results: int) -> list[IngestSource]:
        response = await client.get("https://data.gdacs.org/xml/rss_7d.xml")
        response.raise_for_status()
        root = ET.fromstring(response.text)
        sources: list[IngestSource] = []
        for item in root.findall(".//item")[:max_results]:
            title = self._xml_text(item, "title") or "GDACS disaster alert"
            url = self._xml_text(item, "link")
            if not url:
                continue
            description = self._plain_text(self._xml_text(item, "description") or title)
            latitude = self._optional_float(self._xml_text(item, "lat"))
            longitude = self._optional_float(self._xml_text(item, "long") or self._xml_text(item, "lon"))
            sources.append(
                IngestSource(
                    title=title[:255],
                    url=url,
                    publisher="GDACS",
                    published_at=self._parse_rfc2822(self._xml_text(item, "pubDate")),
                    raw_text=f"{title}. {description}",
                    category=self._infer_category(title, description),
                    location=self._location_from_title(title),
                    latitude=latitude,
                    longitude=longitude,
                )
            )
        return sources

    async def _fetch_conflict_news(
        self, client: httpx.AsyncClient, max_results: int, *, skip_gdelt: bool = False
    ) -> list[FeedOutcome]:
        """GDELT first, Google News RSS if it fails or its circuit is open.

        Returns the conflict_news outcome, followed by GDELT's own outcome when
        GDELT was actually tried. A skipped run reports nothing for GDELT, so its
        failure timestamp — and therefore the cooldown — is left untouched.
        """
        gdelt: FeedOutcome | None = None
        if not skip_gdelt:
            try:
                items = await self._fetch_gdelt_conflict_news(client, max_results)
                return [FeedOutcome(feed="conflict_news", items=items), FeedOutcome(feed=GDELT_FEED, items=items)]
            except Exception as exc:
                logger.warning("GDELT conflict feed failed; falling back to Google News RSS: %s", exc)
                gdelt = FeedOutcome(feed=GDELT_FEED, error=short_error(exc))

        if gdelt is None:
            note = "Served by the Google News fallback; GDELT skipped while its circuit breaker cools down"
        else:
            note = f"Served by the Google News fallback; GDELT failed ({gdelt.error})"
        try:
            items = await self._fetch_google_conflict_news(client, max_results)
            conflict = FeedOutcome(feed="conflict_news", items=items, note=note)
        except Exception as exc:
            logger.warning("Public feed conflict_news failed: %s", exc)
            conflict = FeedOutcome(feed="conflict_news", error=short_error(exc))
        # GDELT's failure is kept even when the fallback fails too, so the breaker still opens.
        return [conflict] if gdelt is None else [conflict, gdelt]

    async def _fetch_gdelt_conflict_news(self, client: httpx.AsyncClient, max_results: int) -> list[IngestSource]:
        response = await client.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": '("armed conflict" OR airstrike OR "missile attack" OR shelling OR ceasefire)',
                "mode": "artlist",
                "format": "json",
                "sort": "datedesc",
                "timespan": "24h",
                "maxrecords": max_results,
            },
        )
        response.raise_for_status()
        articles = response.json().get("articles", [])
        sources: list[IngestSource] = []
        for article in articles[:max_results]:
            title = str(article.get("title") or "Conflict situation update").strip()
            url = article.get("url")
            if not url:
                continue
            publisher = str(article.get("domain") or "GDELT indexed publisher")
            source_country = str(article.get("sourcecountry") or "").strip()
            context = f" Published by a source in {source_country}." if source_country else ""
            sources.append(
                IngestSource(
                    title=title[:255],
                    url=url,
                    publisher=publisher[:128],
                    published_at=self._parse_gdelt_datetime(article.get("seendate")),
                    raw_text=(
                        f"{title}. Conflict-related news coverage indexed by GDELT from {publisher}."
                        f"{context}"
                    ),
                    category="Conflict",
                    location="Global",
                )
            )
        return sources

    async def _fetch_google_conflict_news(self, client: httpx.AsyncClient, max_results: int) -> list[IngestSource]:
        response = await client.get(
            "https://news.google.com/rss/search",
            params={
                "q": '("armed conflict" OR airstrike OR "missile attack" OR shelling OR ceasefire) when:1d',
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            },
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        sources: list[IngestSource] = []
        for item in root.findall(".//item")[:max_results]:
            title = self._xml_text(item, "title") or "Conflict situation update"
            url = self._xml_text(item, "link")
            if not url:
                continue
            publisher = self._xml_text(item, "source") or "Google News indexed publisher"
            description = self._plain_text(self._xml_text(item, "description") or title)
            sources.append(
                IngestSource(
                    title=title[:255],
                    url=url,
                    publisher=publisher[:128],
                    published_at=self._parse_rfc2822(self._xml_text(item, "pubDate")),
                    raw_text=f"{title}. Conflict-related news coverage from {publisher}. {description[:500]}",
                    category="Conflict",
                    location="Global",
                )
            )
        return sources

    async def _fetch_newsapi(self, query: str, max_results: int) -> list[IngestSource]:
        if not settings.news_api_key:
            raise ValueError("NEWS_API_KEY is not configured")
        params = {
            "q": query,
            "pageSize": max_results,
            "language": "en",
            "sortBy": "publishedAt",
            "apiKey": settings.news_api_key,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get("https://newsapi.org/v2/everything", params=params)
            response.raise_for_status()
        articles = response.json().get("articles", [])
        return [
            IngestSource(
                title=article.get("title") or "Untitled crisis report",
                url=article.get("url"),
                publisher=(article.get("source") or {}).get("name") or "NewsAPI",
                published_at=self._parse_datetime(article.get("publishedAt")),
                raw_text=" ".join(part for part in [article.get("description"), article.get("content")] if part),
            )
            for article in articles
            if article.get("url") and (article.get("description") or article.get("content"))
        ]

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _datetime_from_millis(self, value: int | float | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)

    def _plain_text(self, value: str) -> str:
        without_tags = re.sub(r"<[^>]+>", " ", value)
        return " ".join(without_tags.split())

    def _parse_rfc2822(self, value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _parse_gdelt_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _optional_float(self, value: str | None) -> float | None:
        if value in {None, ""}:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _xml_text(self, item: ET.Element, local_name: str) -> str | None:
        for child in item.iter():
            if child.tag.split("}")[-1].lower() == local_name.lower() and child.text:
                return child.text.strip()
        return None

    def _location_from_title(self, title: str) -> str:
        parts = [part.strip() for part in re.split(r"\s+-\s+|\s+in\s+", title, maxsplit=1) if part.strip()]
        return parts[-1][:255] if len(parts) > 1 else "Global"

    def _first_name(self, value) -> str | None:
        if isinstance(value, list) and value:
            return self._first_name(value[0])
        if isinstance(value, dict):
            name = value.get("name")
            return str(name) if name else None
        if isinstance(value, str):
            return value
        return None

    def _infer_category(self, title: str, text: str) -> str:
        content = f"{title} {text}".lower()
        keyword_map = {
            "Flood": ["flood", "rainfall", "river", "cyclone", "storm surge"],
            "Wildfire": ["wildfire", "fire", "hotspot", "smoke"],
            "Health": ["outbreak", "hospital", "disease", "infection", "respiratory"],
            "Cybersecurity": ["cyber", "ransomware", "malware", "breach", "cve"],
            "Financial": ["market", "bank", "inflation", "liquidity", "default"],
            "Earthquake": ["earthquake", "seismic", "aftershock", "magnitude"],
            "Conflict": ["war", "armed conflict", "airstrike", "missile", "shelling", "ceasefire", "troops"],
        }
        for category, keywords in keyword_map.items():
            if any(keyword in content for keyword in keywords):
                return category
        return "General"

    def _infer_location(self, text: str) -> str:
        known_locations = ["India", "United States", "Brazil", "California", "Odisha", "Assam", "Europe"]
        lowered = text.lower()
        return next((location for location in known_locations if location.lower() in lowered), "Unknown")

    def _summarize(self, text: str) -> str:
        compact = " ".join(text.split())
        return compact[:280] + ("..." if len(compact) > 280 else "")

    def _recommended_actions(self, category: str, severity: str) -> list[str]:
        actions_by_category = {
            "Flood": ["Validate affected districts.", "Prepare evacuation and shelter updates.", "Monitor waterborne disease risk."],
            "Wildfire": ["Track perimeter growth.", "Prepare evacuation readiness notices.", "Monitor wind and air quality indicators."],
            "Health": ["Increase testing coverage.", "Monitor hospital capacity.", "Publish verified public health guidance."],
            "Cybersecurity": ["Isolate affected systems.", "Check backups and incident logs.", "Notify response teams and leadership."],
            "Earthquake": ["Assess shaking and damage reports.", "Monitor aftershock risk.", "Check transport and utility disruptions."],
            "Conflict": ["Verify independently reported impacts.", "Track displacement and infrastructure risk.", "Monitor escalation and ceasefire developments."],
        }
        actions = actions_by_category.get(category, ["Verify source credibility.", "Monitor for corroborating reports.", "Prepare an analyst brief."])
        if severity in {"high", "critical"}:
            return ["Escalate to analyst review."] + actions
        return actions

    def _publisher_credibility(self, provider: str) -> float:
        return {"manual": 0.7, "mock": 0.74, "public": 0.82, "gnews": 0.78, "newsapi": 0.78}.get(provider, 0.65)


ingestion_service = IngestionService()
