"""The monitor investigates what warrants it, without being asked."""

import pytest

from app.core.config import settings
from app.services.agent_service import agent_service
from tests.test_alerts import add


@pytest.fixture(autouse=True)
def auto(monkeypatch):
    monkeypatch.setattr(settings, "auto_investigate", True)
    monkeypatch.setattr(settings, "auto_investigate_min_severity", "medium")
    monkeypatch.setattr(settings, "auto_investigate_max_per_sync", 3)


def test_an_incident_at_the_threshold_is_investigated_unprompted(db):
    add(db, "inc-medium", "medium", 46.9)

    assert agent_service.investigate_priorities(db, ["inc-medium"]) == ["inc-medium"]
    runs = agent_service.list_runs(db, "inc-medium")
    assert [run.agent_name for run in runs][:2] == ["Research Agent", "Verification Agent"]
    assert {run.investigation for run in runs} == {1}


def test_a_low_incident_is_left_alone(db):
    add(db, "inc-low", "low", 16.3)

    assert agent_service.investigate_priorities(db, ["inc-low"]) == []
    assert agent_service.list_runs(db, "inc-low") == []


def test_an_incident_already_investigated_is_not_investigated_again(db):
    add(db, "inc-medium", "medium", 46.9)
    agent_service.investigate_priorities(db, ["inc-medium"])

    assert agent_service.investigate_priorities(db, ["inc-medium"]) == []
    assert {run.investigation for run in agent_service.list_runs(db, "inc-medium")} == {1}


def test_the_worst_go_first_within_the_cap(db, monkeypatch):
    monkeypatch.setattr(settings, "auto_investigate_max_per_sync", 2)
    for name, score in (("inc-a", 30.0), ("inc-b", 72.0), ("inc-c", 51.0)):
        add(db, name, "medium" if score < 56 else "high", score)

    assert agent_service.investigate_priorities(db, ["inc-a", "inc-b", "inc-c"]) == ["inc-b", "inc-c"]


def test_one_failure_does_not_stop_the_others(db, monkeypatch):
    add(db, "inc-bad", "high", 72.0)
    add(db, "inc-good", "medium", 46.9)
    real = agent_service.investigate

    def fail_the_first(session, incident_id):
        if incident_id == "inc-bad":
            raise RuntimeError("the model endpoint is down")
        return real(session, incident_id)

    monkeypatch.setattr(agent_service, "investigate", fail_the_first)

    assert agent_service.investigate_priorities(db, ["inc-bad", "inc-good"]) == ["inc-good"]


def test_it_can_be_switched_off(db, monkeypatch):
    monkeypatch.setattr(settings, "auto_investigate", False)
    add(db, "inc-high", "high", 72.0)

    assert agent_service.investigate_priorities(db, ["inc-high"]) == []
