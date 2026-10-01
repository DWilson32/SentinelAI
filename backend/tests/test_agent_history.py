"""Re-investigating an incident keeps the earlier investigations."""

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import AgentRunModel, ReportModel
from app.schemas.ingestion import IngestSource
from app.services import agent_service as agents
from app.services.agent_service import agent_service
from app.services.ingestion_service import ingestion_service
from app.services.report_service import report_service


def ingest_one(db):
    source = IngestSource(
        title="M 6.1 - 20 km N of Hualien City, Taiwan",
        url="https://earthquake.usgs.gov/earthquakes/eventpage/us6000hist",
        publisher="USGS Earthquake Hazards Program",
        published_at=datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc),
        raw_text="USGS reported a magnitude 6.1 earthquake near Hualien City; buildings and a bridge were damaged.",
        category="Earthquake",
        location="20 km N of Hualien City, Taiwan",
        latitude=24.2,
        longitude=121.6,
    )
    return ingestion_service._persist_sources(db, "public", [source]).incidents[0].incident_id


def test_a_second_investigation_adds_to_the_first(db):
    incident_id = ingest_one(db)
    first = agent_service.investigate(db, incident_id)
    second = agent_service.investigate(db, incident_id)

    assert {run.investigation for run in first} == {1}
    assert {run.investigation for run in second} == {2}
    history = agent_service.list_runs(db, incident_id)
    assert len(history) == len(first) + len(second)
    assert {run.id for run in first} <= {run.id for run in history}


def test_runs_from_before_numbering_count_as_the_first(db):
    incident_id = ingest_one(db)
    agent_service.investigate(db, incident_id)
    for run in db.scalars(select(AgentRunModel)).all():
        run.input = {key: value for key, value in run.input.items() if key != "investigation"}
    db.commit()

    assert {run.investigation for run in agent_service.investigate(db, incident_id)} == {2}


def test_history_is_capped_so_public_endpoints_cannot_grow_it_without_limit(db, monkeypatch):
    monkeypatch.setattr(agents, "KEEP_INVESTIGATIONS", 3)
    incident_id = ingest_one(db)
    for _ in range(5):
        agent_service.investigate(db, incident_id)
    report_service.generate_report(db, incident_id)

    kept = sorted({run.investigation for run in agent_service.list_runs(db, incident_id)})
    assert kept == [3, 4, 5]
    assert db.scalar(select(func.count()).select_from(ReportModel)) == 3
