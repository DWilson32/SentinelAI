"""Alerting: when an incident is raised, when it is not, and what delivery records."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.config import settings
from app.db.models import AlertModel, IncidentModel, SourceModel
from app.services import alert_service as module
from app.services.alert_service import alert_service


@pytest.fixture(autouse=True)
def alerting(monkeypatch):
    monkeypatch.setattr(settings, "alerts_enabled", True)
    monkeypatch.setattr(settings, "alert_min_severity", "medium")
    monkeypatch.setattr(settings, "alert_max_per_sync", 10)
    monkeypatch.setattr(settings, "alert_webhook_url", None)


def add(db, incident_id, severity, risk_score, *, sources=2, credibility=0.97, reported_hours_ago=1.0):
    """An incident with one source, reported recently enough to count as active.

    Alerting only covers active incidents, so the source timestamp matters:
    reported_hours_ago past the 72-hour window makes the incident archived.
    """
    now = datetime.now(timezone.utc)
    db.add(
        IncidentModel(
            id=incident_id,
            title=f"Airstrike reported in {incident_id}",
            category="Conflict",
            location="Rakhine, Myanmar",
            latitude=20.1,
            longitude=93.3,
            severity=severity,
            risk_score=risk_score,
            status="monitoring",
            summary="A report.",
            created_at=now,
            updated_at=now,
            recommended_actions=[],
            risk_confidence=0.6,
            risk_drivers=[],
            feature_importance={},
            evidence={"credibility": credibility, "independent_sources": sources, "origins": [], "copies": []},
        )
    )
    db.flush()
    db.add(
        SourceModel(
            id=f"src-{incident_id}",
            incident_id=incident_id,
            title=f"Airstrike reported in {incident_id}",
            url=f"https://news.example/{incident_id}",
            publisher="Reuters",
            credibility_score=0.9,
            published_at=now - timedelta(hours=reported_hours_ago),
            raw_text="A report.",
        )
    )
    db.commit()
    return incident_id


def test_an_incident_at_the_threshold_raises_one_alert(db):
    add(db, "inc-medium", "medium", 46.9)

    raised = alert_service.raise_for(db, ["inc-medium"])

    assert len(raised) == 1
    assert raised[0].severity == "medium"
    assert "2 independent sources" in raised[0].reason
    assert raised[0].delivered_at is None


def test_a_low_incident_raises_nothing(db):
    add(db, "inc-low", "low", 16.3)

    assert alert_service.raise_for(db, ["inc-low"]) == []


def test_the_same_incident_does_not_alert_twice_at_one_severity(db):
    add(db, "inc-medium", "medium", 46.9)
    alert_service.raise_for(db, ["inc-medium"])

    assert alert_service.raise_for(db, ["inc-medium"]) == []


def test_escalation_alerts_again_and_says_so(db):
    add(db, "inc-rising", "medium", 46.9)
    alert_service.raise_for(db, ["inc-rising"])

    incident = db.get(IncidentModel, "inc-rising")
    incident.severity, incident.risk_score = "high", 61.0
    db.commit()
    raised = alert_service.raise_for(db, ["inc-rising"])

    assert [alert.severity for alert in raised] == ["high"]
    assert "escalated to high" in raised[0].reason
    # And not a third time at the same band.
    assert alert_service.raise_for(db, ["inc-rising"]) == []


def test_the_worst_incidents_alert_first_within_the_cap(db, monkeypatch):
    monkeypatch.setattr(settings, "alert_max_per_sync", 2)
    for name, score in (("inc-a", 30.0), ("inc-b", 72.0), ("inc-c", 51.0)):
        add(db, name, "medium" if score < 56 else "high", score)

    raised = alert_service.raise_for(db, ["inc-a", "inc-b", "inc-c"])

    assert [alert.incident_id for alert in raised] == ["inc-b", "inc-c"]


def test_delivery_posts_the_alert_and_records_the_time(db, monkeypatch):
    monkeypatch.setattr(settings, "alert_webhook_url", "https://hooks.example/sentinel")
    sent = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json):
            sent.append((url, json))
            return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(module.httpx, "Client", FakeClient)
    add(db, "inc-medium", "medium", 46.9)
    raised = alert_service.raise_for(db, ["inc-medium"])

    assert alert_service.deliver(db, raised) == 1
    url, payload = sent[0]
    assert url == "https://hooks.example/sentinel"
    assert payload["alert"]["severity"] == "medium"
    assert payload["incident"]["id"] == "inc-medium"
    assert "MEDIUM" in payload["text"]
    assert db.get(AlertModel, raised[0].id).delivered_at is not None


def test_a_failed_delivery_is_recorded_on_the_alert_not_raised(db, monkeypatch):
    monkeypatch.setattr(settings, "alert_webhook_url", "https://hooks.example/sentinel")

    class BrokenClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json):
            raise httpx.ConnectError("nowhere to send it")

    monkeypatch.setattr(module.httpx, "Client", BrokenClient)
    add(db, "inc-medium", "medium", 46.9)
    raised = alert_service.raise_for(db, ["inc-medium"])

    assert alert_service.deliver(db, raised) == 0
    stored = db.get(AlertModel, raised[0].id)
    assert stored.delivered_at is None
    assert "nowhere to send it" in stored.delivery_error


def test_an_unconfigured_webhook_still_leaves_the_alert_standing(db):
    add(db, "inc-medium", "medium", 46.9)
    raised = alert_service.raise_for(db, ["inc-medium"])

    assert alert_service.deliver(db, raised) == 0
    overview = alert_service.overview(db)
    assert overview.delivery_configured is False
    assert overview.unacknowledged == 1
    assert overview.alerts[0].incident_title.startswith("Airstrike")


def test_acknowledging_clears_it_from_the_unacknowledged_count(db):
    add(db, "inc-medium", "medium", 46.9)
    raised = alert_service.raise_for(db, ["inc-medium"])

    acknowledged = alert_service.acknowledge(db, raised[0].id)

    assert acknowledged.acknowledged_at is not None
    assert alert_service.unacknowledged_count(db) == 0
    assert alert_service.overview(db, unacknowledged_only=True).alerts == []
    assert alert_service.acknowledge(db, "alt-missing") is None


def test_alerting_can_be_switched_off(db, monkeypatch):
    monkeypatch.setattr(settings, "alerts_enabled", False)
    add(db, "inc-medium", "medium", 46.9)

    assert alert_service.raise_for(db, ["inc-medium"]) == []


def test_newest_alerts_come_first(db):
    add(db, "inc-a", "medium", 46.9)
    add(db, "inc-b", "high", 61.0)
    first = alert_service.raise_for(db, ["inc-a"])[0]
    first.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()
    alert_service.raise_for(db, ["inc-b"])

    assert [alert.incident_id for alert in alert_service.overview(db).alerts] == ["inc-b", "inc-a"]


def test_an_incident_no_longer_being_reported_does_not_alert(db):
    # Severity is never retired, so the archive fills with old severe incidents.
    add(db, "inc-stale", "high", 72.0, reported_hours_ago=120)

    assert alert_service.pending(db, ["inc-stale"]) == []
    assert alert_service.raise_for(db, ["inc-stale"]) == []
