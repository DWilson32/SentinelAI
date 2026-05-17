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
from app.schemas.risk import RiskPredictionRequest
from app.services.rag_index_service import rag_index_service

logger = logging.getLogger(__name__)


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

    async def ingest_public_feeds(self, db: Session, max_results: int = 12) -> IngestResponse:
        sources: list[IngestSource] = []
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            source_groups = await self._fetch_public_sources(client, max_results)
        for group in source_groups:
            sources.extend(group)
        return self._persist_sources(db, "public", sources[:max_results])

    async def _fetch_public_sources(self, client: httpx.AsyncClient, max_results: int) -> list[list[IngestSource]]:
        tasks = [
            self._fetch_usgs_earthquakes(client, max_results=max(3, max_results // 2)),
            self._fetch_gdacs_events(client, max_results=max(3, max_results // 3)),
            self._fetch_reliefweb_reports(client, max_results=max(3, max_results // 2)),
        ]
        results: list[list[IngestSource]] = []
        for task in tasks:
            try:
                results.append(await task)
            except Exception as exc:
                logger.warning("Public feed ingestion source failed: %s", exc)
                results.append([])
        return results

    def _persist_sources(self, db: Session, provider: str, sources: list[IngestSource]) -> IngestResponse:
        incidents: list[IngestedIncident] = []
        skipped = 0

        for source in sources:
            source_url = str(source.url)
            existing_source = db.scalar(select(SourceModel).where(SourceModel.url == source_url))
            if existing_source is not None:
                existing_incident = db.get(IncidentModel, existing_source.incident_id)
                if existing_incident is not None:
                    incidents.append(
                        IngestedIncident(
                            incident_id=existing_incident.id,
                            title=existing_incident.title,
                            category=existing_incident.category,
                            severity=existing_incident.severity,
                            risk_score=existing_incident.risk_score,
                            source_url=source_url,
                            created=False,
                        )
                    )
                skipped += 1
                continue

            created_at = datetime.now(timezone.utc)
            published_at = source.published_at or created_at
            category = source.category or self._infer_category(source.title, source.raw_text)
            location = source.location or self._infer_location(source.raw_text)
            model_prediction = risk_model.predict(
                RiskPredictionRequest(
                    title=source.title,
                    text=source.raw_text,
                    category=category,
                    source_credibility=self._publisher_credibility(provider),
                    source_count=1,
                )
            )
            risk_score = model_prediction.risk_score
            severity = model_prediction.severity
            incident_id = f"inc-{uuid4().hex[:12]}"

            incident = IncidentModel(
                id=incident_id,
                title=source.title,
                category=category,
                location=location,
                latitude=source.latitude or 0.0,
                longitude=source.longitude or 0.0,
                severity=severity,
                risk_score=risk_score,
                status="investigating" if severity in {"high", "critical"} else "monitoring",
                summary=self._summarize(source.raw_text),
                created_at=created_at,
                updated_at=created_at,
                recommended_actions=self._recommended_actions(category, severity),
                risk_confidence=model_prediction.confidence,
                risk_drivers=model_prediction.drivers,
                feature_importance=model_prediction.feature_importance,
            )
            incident.sources = [
                SourceModel(
                    id=f"src-{uuid4().hex[:12]}",
                    title=source.title,
                    url=source_url,
                    publisher=source.publisher,
                    credibility_score=self._publisher_credibility(provider),
                    published_at=published_at,
                    raw_text=source.raw_text,
                )
            ]
            incident.timeline = [
                TimelineEventModel(
                    timestamp=created_at,
                    label="Ingested",
                    description=f"Incident created from {provider} source: {source.publisher}.",
                )
            ]

            db.add(incident)
            incidents.append(
                IngestedIncident(
                    incident_id=incident.id,
                    title=incident.title,
                    category=incident.category,
                    severity=incident.severity,
                    risk_score=incident.risk_score,
                    source_url=source_url,
                    created=True,
                )
            )

        db.commit()
        created_ids = [incident.incident_id for incident in incidents if incident.created]
        try:
            rag_index_service.index_incidents(db, created_ids)
        except Exception as exc:
            logger.warning("Incident ingestion succeeded, but RAG indexing failed: %s", exc)
        created_count = sum(1 for incident in incidents if incident.created)
        return IngestResponse(
            provider=provider,
            created_count=created_count,
            skipped_count=skipped,
            incidents=incidents,
            message=f"Ingested {created_count} new incident(s); skipped {skipped} duplicate source(s).",
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
            params={"appname": "sentinel-ai-local"},
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
        }
        actions = actions_by_category.get(category, ["Verify source credibility.", "Monitor for corroborating reports.", "Prepare an analyst brief."])
        if severity in {"high", "critical"}:
            return ["Escalate to analyst review."] + actions
        return actions

    def _publisher_credibility(self, provider: str) -> float:
        return {"manual": 0.7, "mock": 0.74, "public": 0.82, "gnews": 0.78, "newsapi": 0.78}.get(provider, 0.65)


ingestion_service = IngestionService()
