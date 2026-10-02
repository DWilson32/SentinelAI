"""The language-model gateway: budget, cache and metering, with a fake client."""

from types import SimpleNamespace

import openai
import pytest

from app.agents import llm
from app.core.config import settings
from app.db.database import Base, SessionLocal, engine
from app.db.models import LlmCacheModel, LlmUsageModel


class FakeClient:
    """Stands in for openai.OpenAI; records what it was asked."""

    calls: list[dict] = []
    fail = False

    def __init__(self, **kwargs):
        self.base_url = kwargs.get("base_url")
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **options):
        if FakeClient.fail:
            raise RuntimeError("rate limited")
        FakeClient.calls.append(options)
        content = f"answer {len(FakeClient.calls)}"
        if options.get("response_format") == {"type": "json_object"}:
            content = f'{{"actions": [{{"number": 1, "why": "{content}"}}]}}'
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )


@pytest.fixture
def model(monkeypatch):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.query(LlmUsageModel).delete()
        db.query(LlmCacheModel).delete()
        db.commit()
    FakeClient.calls = []
    FakeClient.fail = False
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_daily_token_budget", 1000)
    return FakeClient


def usage():
    with SessionLocal() as db:
        return db.get(LlmUsageModel, llm._today())


def test_no_key_means_no_call(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
    assert llm.chat_completion("system", "user") is None


def test_a_call_is_counted_against_the_day(model):
    with llm.metered() as meter:
        assert llm.chat_completion("system", "user") == "answer 1"
    assert (meter.calls, meter.tokens) == (1, 30)
    assert (usage().calls, usage().prompt_tokens, usage().completion_tokens) == (1, 20, 10)
    # Groq's endpoint and model, with short reasoning for gpt-oss.
    assert model.calls[0]["model"] == "openai/gpt-oss-120b"
    assert model.calls[0]["reasoning_effort"] == "low"


def test_a_repeated_prompt_is_answered_from_the_cache(model):
    llm.chat_completion("system", "user")
    with llm.metered() as meter:
        assert llm.chat_completion("system", "user") == "answer 1"
    assert len(model.calls) == 1
    assert (meter.cached, meter.calls, usage().cached) == (1, 0, 1)


def test_the_budget_stops_calls_before_the_free_limit(model, monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_token_budget", 30)
    assert llm.chat_completion("system", "first") == "answer 1"
    with llm.metered() as meter:
        assert llm.chat_completion("system", "second") is None
    assert len(model.calls) == 1
    assert (meter.skipped, usage().skipped) == (1, 1)


def test_a_failed_call_falls_back_instead_of_raising(model):
    model.fail = True
    assert llm.chat_completion("system", "user") is None
    assert usage().skipped == 1


def test_an_investigation_records_its_cost_and_who_wrote_each_step(model, db):
    from datetime import datetime, timezone

    from app.schemas.ingestion import IngestSource
    from app.services.agent_service import agent_service
    from app.services.ingestion_service import ingestion_service

    source = IngestSource(
        title="Airstrike near a market in Rakhine state kills 33 people - AP News",
        url="https://news.example/rakhine-airstrike",
        publisher="AP News",
        published_at=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
        raw_text="An airstrike near a market in Rakhine state killed 33 people.",
        category="Conflict",
        location="Global",
    )
    incident_id = ingestion_service._persist_sources(db, "public", [source]).incidents[0].incident_id
    runs = agent_service.investigate(db, incident_id)

    assert runs[0].input["llm"]["calls"] == 5
    assert runs[0].input["llm"]["tokens"] == 150
    assert {run.output["written_by"] for run in runs} == {"openai/gpt-oss-120b"}


def test_an_investigation_leaves_the_incidents_actions_to_the_playbook(model, db):
    from datetime import datetime, timezone

    from app.db.models import IncidentModel
    from app.schemas.ingestion import IngestSource
    from app.services.agent_service import agent_service
    from app.services.ingestion_service import ingestion_service
    from app.services.playbook import recommended_actions

    source = IngestSource(
        title="Sudan official rejects US ceasefire plan, warns of partition - Reuters",
        url="https://news.example/sudan-ceasefire",
        publisher="Reuters",
        published_at=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
        raw_text="A Sudanese official rejected the US ceasefire plan.",
        category="Conflict",
        location="Global",
    )
    incident_id = ingestion_service._persist_sources(db, "public", [source]).incidents[0].incident_id
    playbook = recommended_actions("Conflict", db.get(IncidentModel, incident_id).severity)

    runs = agent_service.investigate(db, incident_id)
    strategy = next(run for run in runs if run.agent_name == "Strategy Agent")

    # The model chose one action for this run; the incident still lists them all.
    assert strategy.output["recommended_actions"] == playbook[:1]
    assert db.get(IncidentModel, incident_id).recommended_actions == playbook
