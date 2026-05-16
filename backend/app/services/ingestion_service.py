from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IncidentModel, SourceModel, TimelineEventModel
from app.schemas.ingestion import ExternalIngestRequest, IngestRequest, IngestResponse, IngestSource, IngestedIncident
from app.services.rag_index_service import rag_index_service


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
            risk_score = self._score_risk(source.title, source.raw_text, category)
            severity = self._severity_from_score(risk_score)
            incident_id = f"inc-{uuid4().hex[:12]}"

            incident = IncidentModel(
                id=incident_id,
                title=source.title,
                category=category,
                location=location,
                latitude=0.0,
                longitude=0.0,
                severity=severity,
                risk_score=risk_score,
                status="investigating" if severity in {"high", "critical"} else "monitoring",
                summary=self._summarize(source.raw_text),
                created_at=created_at,
                updated_at=created_at,
                recommended_actions=self._recommended_actions(category, severity),
                risk_confidence=0.62,
                risk_drivers=self._risk_drivers(source.raw_text, category),
                feature_importance=self._feature_importance(category),
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
        rag_index_service.index_incidents(db, created_ids)
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

    def _infer_category(self, title: str, text: str) -> str:
        content = f"{title} {text}".lower()
        keyword_map = {
            "Flood": ["flood", "rainfall", "river", "cyclone", "storm surge"],
            "Wildfire": ["wildfire", "fire", "hotspot", "smoke"],
            "Health": ["outbreak", "hospital", "disease", "infection", "respiratory"],
            "Cybersecurity": ["cyber", "ransomware", "malware", "breach", "cve"],
            "Financial": ["market", "bank", "inflation", "liquidity", "default"],
        }
        for category, keywords in keyword_map.items():
            if any(keyword in content for keyword in keywords):
                return category
        return "General"

    def _infer_location(self, text: str) -> str:
        known_locations = ["India", "United States", "Brazil", "California", "Odisha", "Assam", "Europe"]
        lowered = text.lower()
        return next((location for location in known_locations if location.lower() in lowered), "Unknown")

    def _score_risk(self, title: str, text: str, category: str) -> float:
        content = f"{title} {text}".lower()
        urgent_terms = ["critical", "evacuation", "outage", "warning", "rapid", "affected", "emergency", "ransomware"]
        score = 42 + min(35, 7 * sum(1 for term in urgent_terms if term in content))
        if category in {"Flood", "Wildfire", "Cybersecurity"}:
            score += 8
        return float(min(100, score))

    def _severity_from_score(self, score: float) -> str:
        if score >= 85:
            return "critical"
        if score >= 70:
            return "high"
        if score >= 50:
            return "medium"
        return "low"

    def _summarize(self, text: str) -> str:
        compact = " ".join(text.split())
        return compact[:280] + ("..." if len(compact) > 280 else "")

    def _risk_drivers(self, text: str, category: str) -> list[str]:
        drivers = [f"{category} keyword signal"]
        if any(term in text.lower() for term in ["evacuation", "warning", "emergency"]):
            drivers.append("Urgent response language")
        if any(term in text.lower() for term in ["hospital", "outage", "shelter"]):
            drivers.append("Critical infrastructure impact")
        return drivers

    def _feature_importance(self, category: str) -> dict[str, float]:
        return {
            "keyword_urgency": 0.34,
            "category_prior": 0.22,
            "source_credibility": 0.2,
            "infrastructure_terms": 0.14,
            "recency": 0.1,
        }

    def _recommended_actions(self, category: str, severity: str) -> list[str]:
        actions_by_category = {
            "Flood": ["Validate affected districts.", "Prepare evacuation and shelter updates.", "Monitor waterborne disease risk."],
            "Wildfire": ["Track perimeter growth.", "Prepare evacuation readiness notices.", "Monitor wind and air quality indicators."],
            "Health": ["Increase testing coverage.", "Monitor hospital capacity.", "Publish verified public health guidance."],
            "Cybersecurity": ["Isolate affected systems.", "Check backups and incident logs.", "Notify response teams and leadership."],
        }
        actions = actions_by_category.get(category, ["Verify source credibility.", "Monitor for corroborating reports.", "Prepare an analyst brief."])
        if severity in {"high", "critical"}:
            return ["Escalate to analyst review."] + actions
        return actions

    def _publisher_credibility(self, provider: str) -> float:
        return {"manual": 0.7, "mock": 0.74, "gnews": 0.78, "newsapi": 0.78}.get(provider, 0.65)


ingestion_service = IngestionService()

