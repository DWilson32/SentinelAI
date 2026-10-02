"""Reports that disagree on the death toll send the investigation back for a second pass."""

import json
from datetime import datetime, timezone

from app.agents import investigation_graph as graph
from app.db.models import SourceModel
from app.schemas.ingestion import IngestSource
from app.services.agent_service import agent_service
from app.services.casualties import toll_history
from app.services.ingestion_service import ingestion_service

# The live Myanmar airstrike: one event, its toll counted up over a day.
RISING = [
    {"title": "Airstrike near a market in Rakhine state kills 33 people - The Washington Post",
     "publisher": "The Washington Post", "published_at": "2026-09-28T16:28:00+00:00"},
    {"title": "Airstrike near a market in Rakhine state kills 49 people - AP News",
     "publisher": "AP News", "published_at": "2026-09-29T01:14:00+00:00"},
    {"title": "Death toll from Myanmar airstrike in Rakhine state rises to 50 - Reuters",
     "publisher": "Reuters", "published_at": "2026-09-29T10:10:00+00:00"},
]
# The live Gaza pair: two attacks on consecutive days, merged as one incident.
CONFLICTING = [
    {"title": "3 Palestinians killed in Israeli strike, gunfire in Gaza despite ceasefire - Anadolu Ajansı",
     "publisher": "Anadolu Ajansı", "published_at": "2026-09-29T06:59:00+00:00"},
    {"title": "Israeli attacks kill Palestinian woman, injure 5 others in Gaza despite ceasefire - Anadolu Ajansı",
     "publisher": "Anadolu Ajansı", "published_at": "2026-09-30T06:22:00+00:00"},
]


def investigate(monkeypatch, sources, answer=lambda system, user: None):
    monkeypatch.setattr(graph, "chat_completion", lambda system, user, **kwargs: answer(system, user))
    incident = {"title": sources[0]["title"], "category": "Conflict", "severity": "medium", "sources": sources}
    return graph.build_investigation_graph().invoke({"incident": incident, "rag_context": "", "steps": []})["steps"]


def test_a_toll_that_only_grows_is_rising():
    toll = toll_history(RISING)

    assert toll["status"] == "rising"
    assert [report["deaths"] for report in toll["reports"]] == [33, 49, 50]


def test_a_later_lower_toll_is_conflicting():
    assert toll_history(CONFLICTING)["status"] == "conflicting"


def test_reports_that_agree_or_give_no_toll_raise_nothing():
    agreeing = [dict(RISING[2]), dict(RISING[2], publisher="Arab News")]
    no_toll = [{"title": "Sudan official rejects US ceasefire plan", "publisher": "Reuters", "published_at": "x"}]

    assert toll_history(agreeing) is None
    assert toll_history(no_toll) is None


def test_disagreeing_tolls_go_back_to_research_once(monkeypatch):
    steps = investigate(monkeypatch, CONFLICTING)

    assert [step["agent_name"] for step in steps] == [
        "Research Agent", "Verification Agent", "Research Agent", "Verification Agent",
        "Prediction Agent", "Strategy Agent", "Report Agent",
    ]
    assert steps[1]["output"]["toll"]["status"] == "conflicting"
    assert steps[2]["output"]["pass"] == 2
    assert steps[3]["output"]["current_toll"] is None  # unresolved without a model


def test_without_a_model_a_rising_toll_settles_on_the_latest_figure(monkeypatch):
    steps = investigate(monkeypatch, RISING)

    assert steps[3]["output"]["current_toll"] == 50
    assert "50" in steps[-1]["output"]["brief"]


def test_agreeing_reports_go_straight_on(monkeypatch):
    steps = investigate(monkeypatch, RISING[2:])

    assert [step["agent_name"] for step in steps] == [
        "Research Agent", "Verification Agent", "Prediction Agent", "Strategy Agent", "Report Agent",
    ]


def test_the_check_may_only_settle_on_a_reported_figure(monkeypatch):
    def answer(system, user):
        if "researcher has examined" in system:
            return json.dumps({"same_event": "yes", "current_toll": 70, "finding": "The toll is now 70."})
        return "A finding."

    check = investigate(monkeypatch, RISING, answer)[3]["output"]

    assert check["same_event"] == "yes"
    assert check["current_toll"] is None


def test_the_second_pass_is_stored_in_order(monkeypatch, db):
    monkeypatch.setattr(graph, "chat_completion", lambda *args, **kwargs: None)
    first = IngestSource(
        title=CONFLICTING[0]["title"],
        url="https://news.example/gaza-1",
        publisher="Anadolu Ajansı",
        published_at=datetime(2026, 9, 29, 6, 59, tzinfo=timezone.utc),
        raw_text="Three Palestinians were killed.",
        category="Conflict",
        location="Gaza",
    )
    incident_id = ingestion_service._persist_sources(db, "public", [first]).incidents[0].incident_id
    db.add(SourceModel(
        id="src-gaza-2", incident_id=incident_id, title=CONFLICTING[1]["title"], url="https://news.example/gaza-2",
        publisher="Anadolu Ajansı", credibility_score=0.8,
        published_at=datetime(2026, 9, 30, 6, 22, tzinfo=timezone.utc), raw_text="A woman was killed.",
    ))
    db.commit()

    agent_service.investigate(db, incident_id)
    runs = agent_service.list_runs(db, incident_id)

    assert [run.agent_name for run in runs][:4] == [
        "Research Agent", "Verification Agent", "Research Agent", "Verification Agent",
    ]
    assert [run.input["step"] for run in runs] == list(range(1, 8))
