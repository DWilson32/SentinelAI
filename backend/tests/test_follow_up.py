"""The work after a sync: its own call, idempotent, and not in the sync's way."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.db.models import AlertModel
from app.schemas.ingestion import IngestRequest, IngestSource
from app.services.agent_service import agent_service
from app.services.alert_service import alert_service
from app.services.ingestion_service import ingestion_service
from app.services.monitor_service import monitor_service


@pytest.fixture(autouse=True)
def unprompted_work_on(monkeypatch):
    monkeypatch.setattr(settings, "auto_investigate", True)
    monkeypatch.setattr(settings, "auto_investigate_min_severity", "medium")
    monkeypatch.setattr(settings, "auto_investigate_max_per_sync", 3)
    monkeypatch.setattr(settings, "alerts_enabled", True)
    monkeypatch.setattr(settings, "alert_min_severity", "medium")
    monkeypatch.setattr(settings, "alert_webhook_url", None)


def source(title, url, *, hours_ago=1.0):
    return IngestSource(
        title=title,
        url=url,
        publisher="AP News",
        published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        raw_text=f"{title}.",
        category="Conflict",
        location="Rakhine, Myanmar",
    )


SERIOUS = source(
    "Airstrike near a market in Rakhine state kills 33 people - AP News",
    "https://news.example/rakhine-airstrike",
)
ROUTINE = source(
    "Trade ministers meet to discuss regional shipping routes - AP News",
    "https://news.example/shipping-talks",
)


def ingest(db, sources):
    return asyncio.run(ingestion_service.ingest_manual(db, IngestRequest(provider="manual", sources=sources)))


def test_a_sync_only_stores(db):
    response = ingest(db, [SERIOUS, ROUTINE])

    assert response.created_count == 2
    # Nothing was investigated or alerted while the sync was running.
    assert db.query(AlertModel).count() == 0
    for incident in response.incidents:
        assert agent_service.list_runs(db, incident.incident_id) == []


def test_the_follow_up_investigates_and_alerts_on_what_crossed_the_threshold(db):
    ingest(db, [SERIOUS, ROUTINE])

    result = monitor_service.follow_up(db)

    # Both are active; only the airstrike carries a death toll, so only it is acted on.
    assert result.considered == 2
    assert (result.investigated, result.alerted) == (1, 1)
    # Nothing is delivered without a configured endpoint, and the alert still stands.
    assert (result.delivered, result.delivery_configured) == (0, False)
    assert alert_service.unacknowledged_count(db) == 1

    alert = db.query(AlertModel).one()
    assert alert.severity == "medium"
    assert agent_service.list_runs(db, alert.incident_id) != []


def test_running_the_follow_up_again_does_nothing(db):
    ingest(db, [SERIOUS])
    monitor_service.follow_up(db)

    repeat = monitor_service.follow_up(db)

    assert (repeat.investigated, repeat.alerted) == (0, 0)
    assert db.query(AlertModel).count() == 1


def test_it_leaves_incidents_that_are_no_longer_reported_alone(db):
    # Stored once, last reported five days ago: severe, but over.
    ingest(db, [source(SERIOUS.title, "https://news.example/old-airstrike", hours_ago=120)])

    result = monitor_service.follow_up(db)

    assert result.considered == 0
    assert (result.investigated, result.alerted) == (0, 0)


def test_a_failed_investigation_does_not_stop_alerting(db, monkeypatch):
    ingest(db, [SERIOUS])

    def broken(session, incident_ids):
        raise RuntimeError("the model endpoint is down")

    monkeypatch.setattr(agent_service, "investigate_priorities", broken)
    result = monitor_service.follow_up(db)

    assert result.investigated == 0
    assert result.alerted == 1
