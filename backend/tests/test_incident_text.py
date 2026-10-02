"""What an incident reads as: feed text without HTML, and a title with the current toll."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.entity_resolution import MergeGroup, entity_resolver
from app.services.headlines import retitle
from app.services.ingestion_service import ingestion_service
from tests.test_entity_resolution import add_incident

T0 = datetime(2026, 9, 28, 16, 28, tzinfo=timezone.utc)
WAPO = "Airstrike near a market in Myanmar's Rakhine state kills 33 people - The Washington Post"
AP = "Airstrike near a market in Myanmar’s Rakhine state kills 49 people - AP News"
REUTERS = "Death toll from Myanmar airstrike in Rakhine state rises to 50 - Reuters"
UN = "UN alarmed by reports that Myanmar air strike killed 50 - Reuters"


def report(title, hours):
    return SimpleNamespace(title=title, raw_text=f"{title}. Reported.", published_at=T0 + timedelta(hours=hours))


def incident(*reports):
    return SimpleNamespace(title=reports[0].title, summary=reports[0].raw_text, sources=list(reports))


def test_google_news_descriptions_lose_their_entities():
    description = '<a href="https://news.example/x">Sudan official rejects US plan</a>&nbsp;&nbsp;<font color="#6f6f6f">The Media Line</font>'
    assert ingestion_service._plain_text(description) == "Sudan official rejects US plan The Media Line"


def test_the_title_follows_the_toll_to_the_earliest_report_of_its_highest_figure():
    myanmar = incident(report(WAPO, 0), report(AP, 8.8), report(REUTERS, 17.7), report(UN, 27.5))
    retitle(myanmar)
    assert myanmar.title == REUTERS
    assert myanmar.summary.startswith(REUTERS)


def test_a_title_without_a_toll_to_beat_stays():
    no_toll = incident(report("Sudan official rejects US ceasefire plan - The Media Line", 0), report("Ex-PM says Sudan risks partition - Al-Monitor", 3))
    retitle(no_toll)
    assert no_toll.title.startswith("Sudan official")

    already = incident(report(REUTERS, 0), report(UN, 10))
    retitle(already)
    assert already.title == REUTERS


def test_a_merge_brings_the_higher_toll_into_the_title(db):
    from app.schemas.ingestion import IngestSource

    for incident_id, title, hours in (("inc-wapo", WAPO, 0), ("inc-reuters", REUTERS, 17.7)):
        add_incident(
            db,
            incident_id,
            IngestSource(
                title=title,
                url=f"https://news.example/{incident_id}",
                publisher=title.rsplit(" - ", 1)[1],
                published_at=T0 + timedelta(hours=hours),
                raw_text=f"{title}. Reported.",
                category="Conflict",
                location="Rakhine, Myanmar",
            ),
            risk_score=50.0,
        )
    entity_resolver.apply_merges(db, [MergeGroup("inc-wapo", [("inc-reuters", "similar report, 0.95 similarity")])])

    from app.db.models import IncidentModel

    assert db.get(IncidentModel, "inc-wapo").title == REUTERS
