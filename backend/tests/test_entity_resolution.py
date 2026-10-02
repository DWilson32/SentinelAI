"""Entity resolution rules, pinned to the live cases they were calibrated on."""

from datetime import datetime, timedelta, timezone

from app.db.models import IncidentAliasModel, IncidentModel, SourceModel
from app.schemas.ingestion import IngestSource
from app.services.entity_resolution import (
    Report,
    entity_resolver,
    magnitude,
    mostly_latin,
    normalized_title,
    origin_time,
    same_event,
)
from app.services.incident_service import incident_service
from app.services.ingestion_service import ingestion_service

T0 = datetime(2026, 9, 29, 6, 38, tzinfo=timezone.utc)
GAZA_FIRST = "3 Palestinians killed in Israeli strike, gunfire in Gaza despite ceasefire - Anadolu Ajansı"
GAZA_SECOND = "Israeli attacks kill Palestinian woman, injure 5 others in Gaza despite ceasefire - Anadolu Ajansı"
SANDWICH_GDACS = (
    "Green earthquake (Magnitude 4.8M, Depth:35km) in South Sandwich Islands Region "
    "29/09/2026 06:38 UTC, No people affected"
)


def usgs(title, lat, lon, at, event="us6000aaaa"):
    return Report(
        url=f"https://earthquake.usgs.gov/earthquakes/eventpage/{event}",
        title=title,
        category="Earthquake",
        published_at=at,
        latitude=lat,
        longitude=lon,
    )


def gdacs(title, lat, lon, published, event="1500001", kind="EQ", category="Earthquake"):
    return Report(
        url=f"https://www.gdacs.org/report.aspx?eventtype={kind}&eventid={event}",
        title=title,
        category=category,
        published_at=published,
        latitude=lat,
        longitude=lon,
    )


def news(title, at, category="Conflict", url=None):
    return Report(url=url or f"https://news.example/{abs(hash(title))}", title=title, category=category, published_at=at)


class TestParsing:
    def test_magnitude_is_read_from_both_feeds(self):
        assert magnitude("M 4.8 - South Sandwich Islands region") == 4.8
        assert magnitude("Green earthquake (Magnitude 5M, Depth:10km) in Philippines 01/10/2026 05:34 UTC") == 5.0
        assert magnitude("Trump rejects Iran's latest ceasefire proposal - NPR") is None

    def test_gdacs_origin_time_comes_from_its_title_not_its_publish_time(self):
        report = gdacs(SANDWICH_GDACS, -58.1, -25.3, T0 + timedelta(minutes=21))
        assert origin_time(report) == T0

    def test_headline_drops_the_publisher_even_when_it_is_hyphenated(self):
        assert normalized_title(
            "US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Al-Monitor"
        ) == normalized_title("US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Arab News")

    def test_script_detection(self):
        assert mostly_latin("Airstrike near a market in Myanmar's Rakhine state kills 49 people")
        assert not mostly_latin("Обстрел НАН Украины число жертв возросло")


class TestStructuredFeeds:
    def test_one_quake_reported_by_usgs_and_gdacs_matches(self):
        a = usgs("M 4.8 - South Sandwich Islands region", -58.1, -25.3, T0)
        b = gdacs(SANDWICH_GDACS, -58.1, -25.3, T0 + timedelta(minutes=21))
        assert same_event(a, b)

    def test_an_aftershock_hours_later_is_a_separate_event(self):
        # Live: two M4.8 quakes 17 km and 2.4 hours apart, near-identical text.
        a = usgs("M 4.8 - South Sandwich Islands region", -58.1, -25.3, T0)
        b = usgs("M 4.8 - South Sandwich Islands region", -58.25, -25.3, T0 + timedelta(hours=2.4), event="us6000bbbb")
        assert same_event(a, b) is None

    def test_distant_quakes_never_match_however_alike_their_text(self):
        # Live: northern and southern Mid-Atlantic Ridge, 7,850 km apart, scored 0.94.
        a = usgs("M 4.6 - northern Mid-Atlantic Ridge", 30.0, -42.0, T0)
        b = usgs("M 4.6 - southern Mid-Atlantic Ridge", -40.0, -16.0, T0 + timedelta(minutes=2), event="us6000cccc")
        assert same_event(a, b, similarity=0.94) is None

    def test_template_cyclone_alerts_are_never_matched_on_text(self):
        # Live: RACHEL-26 and HANNA-26 alerts scored 0.93.
        a = gdacs("Green notification for tropical cyclone RACHEL-26.", 15.0, -110.0, T0, event="1001329", kind="TC", category="Flood")
        b = gdacs("Green notification for tropical cyclone HANNA-26.", 20.0, 140.0, T0, event="1001330", kind="TC", category="Flood")
        assert same_event(a, b, similarity=0.99) is None

    def test_news_never_matches_a_structured_report(self):
        a = news("M 4.8 - South Sandwich Islands region", T0, category="Earthquake")
        b = usgs("M 4.8 - South Sandwich Islands region", -58.1, -25.3, T0)
        assert same_event(a, b) is None


class TestNews:
    def test_syndicated_copies_match_on_headline(self):
        a = news("US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Reuters", T0)
        b = news(
            "US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Arab News",
            T0 + timedelta(minutes=20),
        )
        assert same_event(a, b) == "same headline"

    def test_a_rewritten_report_of_the_same_story_matches_on_similarity(self):
        a = news("Airstrike near a market in Myanmar's Rakhine state kills 33 people - The Washington Post", T0)
        b = news("Airstrike near a market in Myanmar's Rakhine state kills 49 people - AP News", T0 + timedelta(hours=8.8))
        assert same_event(a, b, similarity=0.95)

    def test_a_later_report_with_fewer_deaths_is_a_different_event(self):
        # Live: two Israeli attacks in Gaza a day apart, merged at 0.94 before this rule.
        a = news(GAZA_FIRST, T0)
        b = news(GAZA_SECOND, T0 + timedelta(hours=23.4))
        assert same_event(a, b, similarity=0.94) is None
        assert same_event(b, a, similarity=0.94) is None

    def test_a_later_report_without_a_toll_still_matches(self):
        a = news("Airstrike near a market in Myanmar's Rakhine state kills 33 people - The Washington Post", T0)
        b = news("UN condemns Myanmar airstrike on market in Rakhine state - Reuters", T0 + timedelta(hours=20))
        assert same_event(a, b, similarity=0.93)

    def test_different_stories_below_the_threshold_stay_apart(self):
        # Live: a Sudan ceasefire plan and Iran's ceasefire talks scored 0.875.
        a = news("US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - Reuters", T0)
        b = news("Iran receives US response to proposal to revive Gulf ceasefire", T0 + timedelta(hours=6.4))
        assert same_event(a, b, similarity=0.875) is None

    def test_reports_more_than_48_hours_apart_stay_apart(self):
        a = news("Trump rejects Iran's latest ceasefire proposal - NPR", T0)
        b = news("Trump rejects Iran's latest ceasefire proposal - CNN", T0 + timedelta(hours=49))
        assert same_event(a, b, similarity=0.99) is None

    def test_non_latin_text_needs_an_identical_headline(self):
        # Live: unrelated Russian and Ukrainian articles scored up to 0.94.
        a = news("Обстрел : Украина тестирует собственные перехватчики реактивных беспилотников", T0)
        b = news("Генсек Совета Европы отреагировал на удар по зданию НАН в Киеве", T0 + timedelta(hours=17))
        assert same_event(a, b, similarity=0.94) is None
        copy = news("Обстрел : Украина тестирует собственные перехватчики реактивных беспилотников", T0 + timedelta(hours=1))
        assert same_event(a, copy) == "same headline"

    def test_categories_must_agree(self):
        a = news("Strong earthquake shakes Tokyo", T0, category="Earthquake")
        b = news("Strong earthquake shakes Tokyo", T0, category="Conflict")
        assert same_event(a, b) is None


def quake_sources():
    usgs_source = IngestSource(
        title="M 5.3 - 0 km NNE of Yokaichiba, Japan",
        url="https://earthquake.usgs.gov/earthquakes/eventpage/us6000tz01",
        publisher="USGS Earthquake Hazards Program",
        published_at=datetime(2026, 10, 1, 12, 27, tzinfo=timezone.utc),
        raw_text="USGS reported a magnitude 5.3 earthquake near Yokaichiba, Japan.",
        category="Earthquake",
        location="0 km NNE of Yokaichiba, Japan",
        latitude=35.72,
        longitude=140.56,
    )
    gdacs_source = IngestSource(
        title="Green earthquake (Magnitude 5.3M, Depth:41.649km) in Japan 01/10/2026 12:27 UTC, 31.4 million in MMI VI",
        url="https://www.gdacs.org/report.aspx?eventtype=EQ&eventid=1500100",
        publisher="GDACS",
        published_at=datetime(2026, 10, 1, 12, 48, tzinfo=timezone.utc),
        raw_text="Green earthquake alert in Japan from GDACS.",
        category="Earthquake",
        location="Japan",
        latitude=35.72,
        longitude=140.56,
    )
    return usgs_source, gdacs_source


def wire_source(publisher, minutes):
    return IngestSource(
        title=f"US ceasefire plan for Sudan could lead to Libya-style partition, adviser says - {publisher}",
        url=f"https://news.example/{publisher.replace(' ', '-').lower()}/sudan-plan",
        publisher=publisher,
        published_at=T0 + timedelta(minutes=minutes),
        raw_text="An adviser said the US ceasefire plan for Sudan could lead to a Libya-style partition.",
        category="Conflict",
        location="Global",
    )


class TestIngest:
    def test_one_quake_from_two_feeds_becomes_one_incident(self, db):
        response = ingestion_service._persist_sources(db, "public", list(quake_sources()))
        assert (response.created_count, response.matched_count) == (1, 1)
        detail = incident_service.get_incident(db, response.incidents[0].incident_id)
        assert detail.source_count == 2
        assert {source.publisher for source in detail.sources} == {"USGS Earthquake Hazards Program", "GDACS"}

    def test_a_syndicated_story_attaches_to_the_incident_across_ingests(self, db):
        first = ingestion_service._persist_sources(db, "public", [wire_source("Reuters", 0)])
        second = ingestion_service._persist_sources(db, "public", [wire_source("Arab News", 25)])
        assert (second.created_count, second.matched_count) == (0, 1)
        assert second.incidents[0].incident_id == first.incidents[0].incident_id
        assert [incident.source_count for incident in incident_service.list_incidents(db)] == [2]

    def test_the_same_url_is_still_skipped(self, db):
        ingestion_service._persist_sources(db, "public", [wire_source("Reuters", 0)])
        again = ingestion_service._persist_sources(db, "public", [wire_source("Reuters", 0)])
        assert (again.created_count, again.matched_count, again.skipped_count) == (0, 0, 1)


def add_incident(db, incident_id, source, risk_score):
    now = datetime.now(timezone.utc)
    db.add(
        IncidentModel(
            id=incident_id,
            title=source.title,
            category=source.category,
            location=source.location,
            # As the ingester stores news without a place.
            latitude=source.latitude or 0.0,
            longitude=source.longitude or 0.0,
            severity="high" if risk_score >= 70 else "medium",
            risk_score=risk_score,
            status="monitoring",
            summary=source.raw_text,
            created_at=now,
            updated_at=now,
            recommended_actions=[],
            risk_confidence=0.5,
            risk_drivers=[],
            feature_importance={},
            sources=[
                SourceModel(
                    id=f"src-{incident_id}",
                    title=source.title,
                    url=str(source.url),
                    publisher=source.publisher,
                    credibility_score=0.8,
                    published_at=source.published_at,
                    raw_text=source.raw_text,
                )
            ],
        )
    )
    db.commit()


class TestBackfill:
    def test_merge_keeps_the_first_report_and_leaves_an_alias(self, db):
        # Incidents stored before entity resolution existed, one per feed.
        usgs_source, gdacs_source = quake_sources()
        add_incident(db, "inc-usgs", usgs_source, risk_score=62.0)
        add_incident(db, "inc-gdacs", gdacs_source, risk_score=71.5)

        groups = entity_resolver.plan_merges(db)
        assert [(group.survivor_id, [member for member, _ in group.members]) for group in groups] == [
            ("inc-usgs", ["inc-gdacs"])
        ]

        entity_resolver.apply_merges(db, groups)
        assert db.get(IncidentModel, "inc-gdacs") is None
        assert db.get(IncidentAliasModel, "inc-gdacs").incident_id == "inc-usgs"

        # The old address resolves, with both sources and the higher risk.
        detail = incident_service.get_incident(db, "inc-gdacs")
        assert detail.id == "inc-usgs"
        assert detail.source_count == 2
        assert detail.risk_score == 71.5
        assert any(event.label == "Merged" for event in detail.timeline)

    def test_a_split_undoes_a_merge_and_takes_back_the_old_id(self, db):
        usgs_source, gdacs_source = quake_sources()
        add_incident(db, "inc-usgs", usgs_source, risk_score=62.0)
        add_incident(db, "inc-gdacs", gdacs_source, risk_score=71.5)
        entity_resolver.apply_merges(db, entity_resolver.plan_merges(db))

        new = entity_resolver.split(db, "inc-usgs", ["src-inc-gdacs"], "a test")
        db.commit()

        assert new.id == "inc-gdacs"
        assert db.get(IncidentAliasModel, "inc-gdacs") is None
        kept = db.get(IncidentModel, "inc-usgs")
        assert [source.id for source in kept.sources] == ["src-inc-usgs"]
        assert [source.id for source in new.sources] == ["src-inc-gdacs"]
        assert (new.latitude, new.longitude) == (kept.latitude, kept.longitude)
        assert [event.label for event in new.timeline] == ["Split"]
        assert kept.timeline[-1].label == "Split"

    def test_a_split_news_report_gets_its_own_place_score_and_evidence(self, db):
        first = IngestSource(
            title=GAZA_FIRST,
            url="https://news.example/gaza-1",
            publisher="Anadolu Ajansı",
            published_at=T0,
            raw_text="Three Palestinians were killed in an Israeli strike and gunfire in Gaza.",
            category="Conflict",
            location="Gaza, Palestinian Territory",
        )
        add_incident(db, "inc-gaza", first, risk_score=60.0)
        db.add(
            SourceModel(
                id="src-gaza-2",
                incident_id="inc-gaza",
                title=GAZA_SECOND,
                url="https://news.example/gaza-2",
                publisher="Anadolu Ajansı",
                credibility_score=0.8,
                published_at=T0 + timedelta(hours=23.4),
                raw_text="Israeli attacks killed a Palestinian woman and injured five others in Gaza.",
            )
        )
        db.commit()

        new = entity_resolver.split(db, "inc-gaza", ["src-gaza-2"], "the later report gives fewer deaths")
        db.commit()

        assert new.id.startswith("inc-") and new.id != "inc-gaza"
        assert new.title == GAZA_SECOND
        assert new.location == "Gaza, Palestinian Territory"
        assert new.evidence["independent_sources"] == 1
        kept = db.get(IncidentModel, "inc-gaza")
        assert kept.title == GAZA_FIRST
        assert kept.evidence["independent_sources"] == 1
        assert new.recommended_actions and kept.recommended_actions

    def test_a_split_must_leave_the_incident_a_report(self, db):
        usgs_source, _ = quake_sources()
        add_incident(db, "inc-usgs", usgs_source, risk_score=62.0)
        try:
            entity_resolver.split(db, "inc-usgs", ["src-inc-usgs"], "a test")
        except ValueError:
            pass
        else:
            raise AssertionError("split moved every report")

    def test_nothing_to_merge_when_every_incident_is_distinct(self, db):
        usgs_source, _ = quake_sources()
        add_incident(db, "inc-usgs", usgs_source, risk_score=62.0)
        add_incident(db, "inc-news", wire_source("Reuters", 0), risk_score=70.0)
        assert entity_resolver.plan_merges(db) == []
