"""Publisher credibility and the severity gate."""

from datetime import datetime, timezone

import pytest

from app.ml.risk_model import risk_model
from app.schemas.ingestion import IngestSource
from app.schemas.risk import RiskPredictionRequest
from app.services.credibility import SourceEvidence, assess, gate, publisher_credibility
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
BULLETIN = SourceEvidence(
    "M 7.4 - 50 km S of Port Moresby, Papua New Guinea",
    "USGS Earthquake Hazards Program",
    0.95,
    url="https://earthquake.usgs.gov/earthquakes/eventpage/us7000a",
)


def news(title, publisher, credibility, minutes=0, source_id=""):
    return SourceEvidence(
        title,
        publisher,
        credibility,
        url=f"https://news.example/{abs(hash((title, publisher)))}",
        id=source_id or publisher,
        published_at=datetime(2026, 10, 1, 9, minutes, tzinfo=timezone.utc),
    )


class TestIndependence:
    def test_reprints_belong_to_the_outlet_that_wrote_them(self):
        # Live: one Reuters story carried by Al-Monitor and Arab News, beside two
        # independent pieces, one of them Al-Monitor's own.
        evidence = assess(
            [
                news("US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Reuters", "Reuters", 0.90, 0),
                news("US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Al-Monitor", "Al-Monitor", 0.80, 5),
                news("US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Arab News", "Arab News", 0.80, 9),
                news("Sudan Official Rejects US Ceasefire Plan, Warns of Libya-Style Partition - The Media Line", "The Media Line", 0.80, 30),
                news("Ex-PM says Sudan risks partition amid US ceasefire push - Al-Monitor", "Al-Monitor", 0.80, 50),
            ]
        )
        assert evidence.independent_sources == 3
        assert sorted(evidence.origins) == ["Al-Monitor", "Reuters", "The Media Line"]
        assert {(c["publisher"], c["copy_of"], c["reason"]) for c in evidence.copies} == {
            ("Al-Monitor", "Reuters", "same headline"),
            ("Arab News", "Reuters", "same headline"),
        }

    def test_an_updated_figure_is_still_the_same_story(self):
        # Live: the Washington Post's "kills 33" is AP's story before its toll rose to 49.
        evidence = assess(
            [
                news("Airstrike near a market in Myanmar's Rakhine state kills 33 people - The Washington Post", "The Washington Post", 0.80, 0),
                news("Airstrike near a market in Myanmar’s Rakhine state kills 49 people - AP News", "AP News", 0.90, 40),
                news("Death toll from Myanmar airstrike in Rakhine state rises to 50 - Reuters", "Reuters", 0.90, 50),
                news("UN alarmed by reports that Myanmar air strike killed 50 - Reuters", "Reuters", 0.90, 59),
            ]
        )
        # Two Reuters articles are one newsroom's reporting.
        assert sorted(evidence.origins) == ["AP News", "Reuters"]
        assert evidence.copies == [
            {"source_id": "The Washington Post", "publisher": "The Washington Post", "copy_of": "AP News", "reason": "near-identical headline"}
        ]

    def test_automated_feeds_count_once(self):
        # GDACS builds its quake alerts from the same seismic data as USGS.
        gdacs = SourceEvidence(
            "Green earthquake (Magnitude 7.4M, Depth:10km) in Papua New Guinea",
            "GDACS",
            0.95,
            url="https://www.gdacs.org/report.aspx?eventtype=EQ&eventid=1",
            id="gdacs",
        )
        evidence = assess([BULLETIN, gdacs])
        assert evidence.independent_sources == 1
        assert evidence.copies[0]["reason"] == "same instrument data"

    def test_social_posts_count_once_however_many(self):
        posts = [news(f"Explosion reported downtown, witness {n}", "facebook.com", 0.30, n) for n in range(5)]
        evidence = assess(posts)
        assert (evidence.independent_sources, evidence.credibility) == (1, 0.3)

    def test_independent_sources_combine(self):
        newsroom = news("Floodwaters cut off villages in Sylhet - The Daily Star", "The Daily Star", 0.80)
        site = news("Hundreds stranded as rivers burst banks near Sylhet", "sylhet-today.example", 0.55)
        assert assess([newsroom, site]).credibility == pytest.approx(1 - 0.20 * 0.45, abs=1e-3)
        other = news("Rivers in Sylhet overflow, families move to shelters", "bd-local.example", 0.55)
        assert assess([site, other]).credibility == pytest.approx(1 - 0.45 * 0.45, abs=1e-3)


class TestGate:
    def test_a_lone_rumour_cannot_be_critical(self):
        # The roadmap's case: a rumour used to score 99.4 and be rated critical.
        severity, note = gate("critical", assess([RUMOUR]))
        assert severity == "medium"
        assert note == "Capped at medium from critical: credibility 30% from 1 independent source; critical needs 90%."

    def test_an_official_bulletin_keeps_its_rating(self):
        assert gate("critical", assess([BULLETIN])) == ("critical", None)

    def test_one_recognised_newsroom_allows_high_but_not_critical(self):
        newsroom = assess([news("Airstrike hits market in Rakhine", "The Washington Post", 0.80)])
        assert gate("high", newsroom) == ("high", None)
        assert gate("critical", newsroom)[0] == "high"

    def test_two_independent_recognised_reports_allow_critical(self):
        evidence = assess(
            [
                news("Airstrike near a market in Rakhine kills 33 - The Washington Post", "The Washington Post", 0.80),
                news("UN alarmed by reports that Myanmar air strike killed 50 - Al Jazeera", "Al Jazeera", 0.80),
            ]
        )
        assert gate("critical", evidence) == ("critical", None)

    def test_a_syndicated_story_is_one_report(self):
        # Live: WEAR-TV and WLUK ran the same syndicated piece.
        evidence = assess(
            [
                news("Iran reviews US ceasefire counterproposal - WEAR-TV", "WEAR-TV", 0.55, 0),
                news("Iran reviews US ceasefire counterproposal - WLUK", "WLUK", 0.55, 3),
            ]
        )
        assert evidence.independent_sources == 1
        assert gate("high", evidence)[0] == "medium"

    def test_two_independent_unrecognised_reports_allow_high(self):
        evidence = assess(
            [
                news("Shelling hits Kharkiv overnight", "kharkiv-today.example", 0.55),
                news("Overnight strikes damage homes in Kharkiv", "ukraine-local.example", 0.55),
            ]
        )
        assert gate("high", evidence) == ("high", None)

    def test_lower_ratings_are_never_raised(self):
        assert gate("low", assess([BULLETIN])) == ("low", None)


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
