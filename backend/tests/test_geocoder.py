"""Headline geocoding, pinned to live headlines and the traps they showed."""

from datetime import datetime, timezone

import pytest

from app.schemas.ingestion import IngestSource
from app.services.geocoder import locate
from app.services.incident_service import incident_service
from app.services.ingestion_service import ingestion_service


def place(headline):
    found = locate(headline)
    return (found.precision, found.label) if found else None


@pytest.mark.parametrize(
    "headline, expected",
    [
        # A city beats the actor's demonym: the shelling was in Kyiv, not Russia.
        (
            "Record-holding rescue worker injured in second Russian shelling attack in Kyiv - Ukrinform",
            ("city", "Kyiv, Ukraine"),
        ),
        # A region beats its country, even when the country follows "in".
        (
            "Airstrike near a market in Myanmar's Rakhine state kills 33 people - The Washington Post",
            ("region", "Rakhine, Myanmar"),
        ),
        # The US is a party, "Libya-style" an adjective: the story is about Sudan.
        (
            "US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Al-Monitor",
            ("country", "Sudan"),
        ),
        ("Israel kills eight in Gaza as ceasefire violations continue a year on - Middle East Eye", ("city", "Gaza, Palestinian Territory")),
        ("Iranian Strike Injured 8 US Marines in Strait of Hormuz, Report Says", ("region", "Strait of Hormuz")),
        ("Six months after supposed ceasefire, Lebanon’s displaced face an uncertain future", ("country", "Lebanon")),
        # The trailing publisher is not the place.
        ("Iran receives US response to proposal to revive Gulf ceasefire - Israel National News", ("country", "Iran")),
        ("Trio of Hormuz attacks disclosed as US-Iran ceasefire talks fail - Lloyd's List", ("region", "Strait of Hormuz")),
        # Older spellings resolve to the Ukrainian cities, not Odessa, Texas.
        ("Drones hit port in Odessa overnight", ("city", "Odesa, Ukraine")),
        ("Explosions heard in Kiev", ("city", "Kyiv, Ukraine")),
    ],
)
def test_live_headlines(headline, expected):
    assert place(headline) == expected


@pytest.mark.parametrize(
    "headline",
    [
        # A newspaper glued to the front of the text is not New York.
        "The New York Times. . At least 50 people were killed and 58 injured when a military jet dropped two bombs",
        # Only the US named: a party to the story, so no place.
        "Homeland Security says US will expand screening",
        # Only a demonym: the actor, not the place.
        "Russian shelling kills three overnight",
        # Common words and first names that are also GeoNames places.
        "Nice weather expected for the Victoria Day parade",
        "Los médicos granadinos que conquistan La Revuelta de David Broncano",
        # Not in Latin script: left for a later step rather than guessed.
        "Обстрел НАН Украины число жертв возросло",
        "Leadership in Crisis: Lessons from Armed Conflict and Devastating Disasters",
    ],
)
def test_headlines_left_unlocated(headline):
    assert place(headline) is None


def test_same_named_city_prefers_the_large_one_over_a_mentioned_actor():
    # Donetsk in Ukraine (900k) over Donetsk in Russia (50k), though Russia is named.
    assert place("Russian forces shell Donetsk again") == ("city", "Donetsk, Ukraine")


def test_a_named_country_chooses_between_comparable_namesakes():
    # Tripoli in Lebanon is a sixth the size of Tripoli in Libya: close enough
    # that naming Lebanon decides it, and without that the larger one wins.
    assert place("Clashes in Tripoli leave three dead")[1] == "Tripoli, Libya"
    assert place("Lebanese troops deploy in Tripoli after clashes")[1] == "Tripoli, Lebanon"


def test_ingest_locates_news_and_keeps_feed_coordinates(db):
    news = IngestSource(
        title="Record-holding rescue worker injured in second Russian shelling attack in Kyiv - Ukrinform",
        url="https://news.example/kyiv-rescue-worker",
        publisher="Ukrinform",
        published_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        raw_text="A rescue worker was injured in a second Russian shelling attack in Kyiv.",
        category="Conflict",
        location="Global",
    )
    quake = IngestSource(
        title="M 4.8 - South Sandwich Islands region",
        url="https://earthquake.usgs.gov/earthquakes/eventpage/us6000geo1",
        publisher="USGS Earthquake Hazards Program",
        published_at=datetime(2026, 10, 1, 6, 38, tzinfo=timezone.utc),
        raw_text="USGS reported a magnitude 4.8 earthquake near the South Sandwich Islands.",
        category="Earthquake",
        location="South Sandwich Islands region",
        latitude=-58.1,
        longitude=-25.3,
    )
    response = ingestion_service._persist_sources(db, "public", [news, quake])
    by_title = {i.title: incident_service.get_incident(db, i.incident_id) for i in response.incidents}

    located = by_title[news.title]
    assert (located.location, located.geo_precision) == ("Kyiv, Ukraine", "city")
    assert (round(located.latitude, 1), round(located.longitude, 1)) == (50.5, 30.5)

    reported = by_title[quake.title]
    assert (reported.geo_precision, reported.latitude) == ("reported", -58.1)
