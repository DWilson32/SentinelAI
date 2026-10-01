"""Publisher credibility and the severity gate."""

from datetime import datetime, timezone

import pytest

from app.ml.risk_model import risk_model
from app.schemas.ingestion import IngestSource
from app.schemas.risk import RiskPredictionRequest
from app.services.credibility import SourceEvidence, gate, publisher_credibility
from app.services.incident_service import incident_service
from app.services.ingestion_service import ingestion_service


@pytest.mark.parametrize(
    "publisher, url, expected",
    [
        ("USGS Earthquake Hazards Program", "https://earthquake.usgs.gov/earthquakes/eventpage/us6000a", 0.95),
        ("GDACS", "https://www.gdacs.org/report.aspx?eventtype=FL&eventid=1", 0.95),
        ("Reuters", "https://news.google.com/rss/articles/abc", 0.90),
        # GDELT reports the domain as the publisher.
        ("afp.com", "https://www.afp.com/en/news/1", 0.90),
        ("economictimes.indiatimes.com", "https://economictimes.indiatimes.com/x", 0.80),
        # Suffixes and spacing in feed names do not hide a known outlet.
        ("Le Monde.fr", "https://news.google.com/rss/articles/def", 0.80),
        ("Bloomberg.com", "https://news.google.com/rss/articles/ghi", 0.80),
        # The aggregator's own host says nothing about the publisher.
        ("Some Local Blog", "https://news.google.com/rss/articles/jkl", 0.55),
        ("facebook.com", "https://news.google.com/rss/articles/mno", 0.30),
    ],
)
def test_publisher_credibility(publisher, url, expected):
    assert publisher_credibility(publisher, url) == expected


def test_an_operator_entered_report_keeps_the_manual_default():
    assert publisher_credibility("Analyst Desk", "https://example.com/brief", provider="manual") == 0.70


def test_credibility_no_longer_moves_the_risk_score():
    # It measures how sure we are of a report, not how bad the event is.
    def score(credibility):
        return risk_model.predict(
            RiskPredictionRequest(
                title="Green flood alert in Guinea",
                text="Green flood alert in Guinea. Population exposure is limited.",
                category="Flood",
                source_credibility=credibility,
            )
        ).risk_score

    assert score(0.10) == score(0.95)


RUMOUR = SourceEvidence("Emergency: missile strike hits city, thousands evacuate", "facebook.com", 0.30)
BULLETIN = SourceEvidence("M 7.4 - 50 km S of Port Moresby, Papua New Guinea", "USGS Earthquake Hazards Program", 0.95)


class TestGate:
    def test_a_lone_rumour_cannot_be_critical(self):
        # The roadmap's case: a rumour at low credibility used to score 99.4, critical.
        severity, note = gate("critical", [RUMOUR])
        assert severity == "medium"
        assert note.startswith("Capped at medium from critical: one report")

    def test_an_official_bulletin_keeps_its_rating(self):
        assert gate("critical", [BULLETIN]) == ("critical", None)

    def test_one_recognised_newsroom_allows_high_but_not_critical(self):
        newsroom = SourceEvidence("Airstrike hits market in Rakhine", "The Washington Post", 0.80)
        assert gate("high", [newsroom]) == ("high", None)
        severity, note = gate("critical", [newsroom])
        assert severity == "high"
        assert "official or wire source" in note

    def test_two_independent_recognised_reports_allow_critical(self):
        a = SourceEvidence("Airstrike near a market in Rakhine kills 33 - The Washington Post", "The Washington Post", 0.80)
        b = SourceEvidence("Death toll from Myanmar airstrike rises to 50 - Al Jazeera", "Al Jazeera", 0.80)
        assert gate("critical", [a, b]) == ("critical", None)

    def test_syndicated_copies_are_not_independent(self):
        # One wire story reprinted by two unrecognised outlets is one report.
        a = SourceEvidence("Iran reviews US ceasefire counterproposal - WEAR-TV", "WEAR-TV", 0.55)
        b = SourceEvidence("Iran reviews US ceasefire counterproposal - WLUK", "WLUK", 0.55)
        severity, note = gate("high", [a, b])
        assert severity == "medium"
        assert "not independent" in note

    def test_two_independent_unrecognised_reports_allow_high(self):
        a = SourceEvidence("Shelling hits Kharkiv overnight", "kharkiv-today.example", 0.55)
        b = SourceEvidence("Overnight strikes damage homes in Kharkiv", "ukraine-local.example", 0.55)
        assert gate("high", [a, b]) == ("high", None)

    def test_user_generated_reports_never_corroborate(self):
        a = SourceEvidence("Huge explosion downtown", "facebook.com", 0.30)
        b = SourceEvidence("Explosion reported downtown", "x.com", 0.30)
        assert gate("high", [a, b])[0] == "medium"

    def test_lower_ratings_are_never_raised(self):
        assert gate("low", [BULLETIN]) == ("low", None)


def source(title, publisher, url, minutes=0, text="An emergency evacuation after a missile airstrike and shelling hit the city."):
    return IngestSource(
        title=title,
        url=url,
        publisher=publisher,
        published_at=datetime(2026, 10, 2, 9, minutes, tzinfo=timezone.utc),
        raw_text=text,
        category="Conflict",
        location="Global",
    )


def test_a_corroborating_report_lifts_the_cap(db):
    rumour = source(
        "Emergency evacuation as missile airstrike and shelling hit Kharkiv",
        "facebook.com",
        "https://www.facebook.com/posts/1",
    )
    first = ingestion_service._persist_sources(db, "public", [rumour])
    capped = incident_service.get_incident(db, first.incidents[0].incident_id)
    assert capped.severity == "medium"
    assert capped.severity_note.startswith("Capped at medium")

    wire = source(
        "Emergency evacuation as missile airstrike and shelling hit Kharkiv - Reuters",
        "Reuters",
        "https://www.reuters.com/world/kharkiv-1",
        minutes=20,
    )
    second = ingestion_service._persist_sources(db, "public", [wire])
    assert second.matched_count == 1
    lifted = incident_service.get_incident(db, capped.id)
    assert lifted.severity_note is None
    assert lifted.severity == risk_model.severity_for(lifted.risk_score)
