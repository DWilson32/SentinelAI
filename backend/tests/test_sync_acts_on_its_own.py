"""A sync, end to end: store the reports, investigate what matters, alert on it."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.db.models import AlertModel
from app.schemas.ingestion import IngestSource
from app.services.agent_service import agent_service
from app.services.alert_service import alert_service
from app.services.ingestion_service import ingestion_service


@pytest.fixture(autouse=True)
def unprompted_work_on(monkeypatch):
    monkeypatch.setattr(settings, "auto_investigate", True)
    monkeypatch.setattr(settings, "auto_investigate_min_severity", "medium")
    monkeypatch.setattr(settings, "auto_investigate_max_per_sync", 3)
    monkeypatch.setattr(settings, "alerts_enabled", True)
    monkeypatch.setattr(settings, "alert_min_severity", "medium")
    monkeypatch.setattr(settings, "alert_webhook_url", None)


SERIOUS = IngestSource(
    title="Airstrike near a market in Rakhine state kills 33 people - AP News",
    url="https://news.example/rakhine-airstrike",
    publisher="AP News",
    published_at=datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc),
    raw_text="An airstrike near a market in Rakhine state killed 33 people.",
    category="Conflict",
    location="Rakhine, Myanmar",
)
ROUTINE = IngestSource(
    title="Trade ministers meet to discuss regional shipping routes - Reuters",
    url="https://news.example/shipping-talks",
    publisher="Reuters",
    published_at=datetime(2026, 10, 30, 9, 5, tzinfo=timezone.utc),
    raw_text="Ministers met to discuss regional shipping routes.",
    category="Conflict",
    location="Global",
)


def test_a_sync_investigates_and_alerts_on_what_crossed_the_threshold(db):
    response = asyncio.run(ingestion_service.ingest_manual(db, _request([SERIOUS, ROUTINE])))

    assert response.created_count == 2
    # The airstrike carries a death toll, so it is rated medium and acted on.
    assert response.investigated_count == 1
    assert response.alerted_count == 1
    # Nothing is delivered without a configured endpoint, and the alert still stands.
    assert response.delivered_count == 0
    assert "Investigated 1 unprompted; raised 1 alert(s), delivered 0." in response.message

    alert = db.query(AlertModel).one()
    assert alert.severity == "medium"
    assert "Rakhine" in alert.reason
    assert alert_service.unacknowledged_count(db) == 1

    investigated = [
        incident.incident_id
        for incident in response.incidents
        if agent_service.list_runs(db, incident.incident_id)
    ]
    assert investigated == [response.incidents[0].incident_id]


def test_a_second_sync_of_the_same_story_does_not_alert_or_investigate_again(db):
    asyncio.run(ingestion_service.ingest_manual(db, _request([SERIOUS])))
    again = SERIOUS.model_copy(update={"url": "https://news.example/rakhine-airstrike-2"})

    response = asyncio.run(ingestion_service.ingest_manual(db, _request([again])))

    # Entity resolution attaches it to the same incident, which is already handled.
    assert (response.created_count, response.matched_count) == (0, 1)
    assert (response.investigated_count, response.alerted_count) == (0, 0)
    assert db.query(AlertModel).count() == 1


def _request(sources):
    from app.schemas.ingestion import IngestRequest

    return IngestRequest(provider="manual", sources=sources)
