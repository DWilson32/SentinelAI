"""The Strategy Agent orders the playbook for an incident; it cannot add to it."""

import json

from app.agents import investigation_graph as graph
from app.services.playbook import recommended_actions

PLAYBOOK = recommended_actions("Conflict", "low")


def strategy(monkeypatch, answer: str | None) -> dict:
    monkeypatch.setattr(graph, "chat_completion", lambda *args, **kwargs: answer)
    incident = {"title": "Sudan official rejects US ceasefire plan", "category": "Conflict", "severity": "low"}
    return graph._strategy_node({"incident": incident, "rag_context": "", "steps": []})["steps"][0]["output"]


def test_the_model_orders_playbook_actions_and_says_why(monkeypatch):
    answer = {"actions": [{"number": 3, "why": "The reports concern a ceasefire plan."}, {"number": 1, "why": "x"}]}
    output = strategy(monkeypatch, json.dumps(answer))

    assert output["recommended_actions"] == [PLAYBOOK[2], PLAYBOOK[0]]
    assert f"{PLAYBOOK[2]} The reports concern a ceasefire plan." in output["finding"]
    assert output["written_by"] != "template"


def test_an_action_outside_the_playbook_is_dropped(monkeypatch):
    answer = {"actions": [{"number": 7, "why": "Set up a logistics hub near the border."}, {"number": 2, "why": "x"}]}
    output = strategy(monkeypatch, json.dumps(answer))

    assert output["recommended_actions"] == [PLAYBOOK[1]]
    assert "logistics hub" not in output["finding"]


def test_a_ranked_list_in_prose_is_not_read_as_a_choice(monkeypatch):
    # The live model's prose: its own rank first, then the playbook number.
    output = strategy(monkeypatch, "1. **3 - Monitor escalation and ceasefire developments.** The plan ...")

    assert output["recommended_actions"] == PLAYBOOK
    assert output["written_by"] == "template"


def test_without_a_model_the_playbook_stands_as_written(monkeypatch):
    output = strategy(monkeypatch, None)

    assert output["recommended_actions"] == PLAYBOOK
    assert output["finding"] == "Strategy recommendations derived from incident playbook."
