"""The dashboard asks only for incidents still being reported."""

from app.db.models import IncidentModel
from app.services.incident_service import incident_service
from tests.test_alerts import add


def test_active_only_leaves_out_incidents_no_longer_reported(db):
    add(db, "inc-now", "medium", 46.9, reported_hours_ago=2)
    add(db, "inc-old", "high", 72.0, reported_hours_ago=200)

    assert {i.id for i in incident_service.list_incidents(db)} == {"inc-now", "inc-old"}
    assert [i.id for i in incident_service.list_incidents(db, active_only=True)] == ["inc-now"]


def test_floods_keep_their_longer_window(db):
    # Floods stay active for 7 days, longer than the 72-hour default.
    add(db, "inc-flood", "medium", 40.0, reported_hours_ago=120)
    flood = db.get(IncidentModel, "inc-flood")
    flood.category = "Flood"
    db.commit()

    assert [i.id for i in incident_service.list_incidents(db, active_only=True)] == ["inc-flood"]
