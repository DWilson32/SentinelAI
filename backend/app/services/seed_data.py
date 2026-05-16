from datetime import datetime, timedelta, timezone

from app.schemas.incident import IncidentDetail, RiskExplanation, Source, TimelineEvent

now = datetime.now(timezone.utc)

INCIDENTS: list[IncidentDetail] = [
    IncidentDetail(
        id="inc-001",
        title="Severe flood warnings across Assam river districts",
        category="Flood",
        location="Assam, India",
        latitude=26.2006,
        longitude=92.9376,
        severity="critical",
        risk_score=91,
        status="escalated",
        summary="River levels are above danger marks in multiple districts, with evacuation shelters nearing capacity.",
        created_at=now - timedelta(hours=9),
        updated_at=now - timedelta(minutes=24),
        sources=[
            Source(
                id="src-001",
                title="Flood bulletin reports rising Brahmaputra levels",
                url="https://example.com/assam-flood-bulletin",
                publisher="Regional Disaster Desk",
                credibility_score=0.88,
                published_at=now - timedelta(hours=2),
                raw_text="District officials reported water levels above danger marks and asked vulnerable communities to move to shelters.",
            ),
            Source(
                id="src-002",
                title="Hospitals prepare for flood-linked disease risk",
                url="https://example.com/flood-health-risk",
                publisher="Health Watch",
                credibility_score=0.81,
                published_at=now - timedelta(hours=4),
                raw_text="Health teams warned of waterborne disease risk if flooding persists for more than 48 hours.",
            ),
        ],
        timeline=[
            TimelineEvent(
                timestamp=now - timedelta(hours=9),
                label="Initial alert",
                description="Automated monitoring detected a spike in flood-related reports.",
            ),
            TimelineEvent(
                timestamp=now - timedelta(hours=3),
                label="Severity escalation",
                description="Risk score crossed critical threshold after shelter capacity and rainfall indicators worsened.",
            ),
        ],
        recommended_actions=[
            "Prioritize evacuation for low-lying villages near riverbanks.",
            "Pre-position water purification supplies and mobile health units.",
            "Publish verified district-level shelter availability updates every 3 hours.",
        ],
        risk_explanation=RiskExplanation(
            confidence=0.84,
            drivers=["High rainfall intensity", "Multiple credible sources", "Shelter capacity pressure"],
            feature_importance={"rainfall_intensity": 0.31, "source_volume": 0.24, "population_exposure": 0.22, "health_risk": 0.13},
        ),
    ),
    IncidentDetail(
        id="inc-002",
        title="Wildfire perimeter expands near coastal residential zones",
        category="Wildfire",
        location="Southern California, USA",
        latitude=34.0522,
        longitude=-118.2437,
        severity="high",
        risk_score=78,
        status="investigating",
        summary="Satellite heat signatures and local reports indicate rapid perimeter growth near residential corridors.",
        created_at=now - timedelta(hours=14),
        updated_at=now - timedelta(minutes=51),
        sources=[
            Source(
                id="src-003",
                title="Thermal satellite pass identifies new hotspots",
                url="https://example.com/wildfire-hotspots",
                publisher="Earth Observation Lab",
                credibility_score=0.9,
                published_at=now - timedelta(hours=1),
                raw_text="Thermal detections increased along the western edge, raising concern about wind-driven spread.",
            )
        ],
        timeline=[
            TimelineEvent(
                timestamp=now - timedelta(hours=14),
                label="Hotspot detected",
                description="Thermal anomaly cluster appeared in satellite feed.",
            ),
            TimelineEvent(
                timestamp=now - timedelta(hours=1),
                label="Residential risk",
                description="Perimeter projection moved within 6 km of residential zones.",
            ),
        ],
        recommended_actions=[
            "Issue targeted evacuation readiness notices for western residential corridors.",
            "Increase aerial reconnaissance frequency during peak wind window.",
            "Coordinate traffic control points before evacuation orders are needed.",
        ],
        risk_explanation=RiskExplanation(
            confidence=0.79,
            drivers=["Thermal hotspot growth", "Wind forecast", "Residential proximity"],
            feature_importance={"hotspot_growth": 0.34, "wind_speed": 0.27, "residential_proximity": 0.25, "source_credibility": 0.14},
        ),
    ),
    IncidentDetail(
        id="inc-003",
        title="Hospital admissions rise after respiratory illness cluster",
        category="Health",
        location="Sao Paulo, Brazil",
        latitude=-23.5558,
        longitude=-46.6396,
        severity="medium",
        risk_score=63,
        status="monitoring",
        summary="Admissions for respiratory symptoms are increasing, but lab confirmation and spread indicators remain limited.",
        created_at=now - timedelta(days=1, hours=3),
        updated_at=now - timedelta(hours=2),
        sources=[
            Source(
                id="src-004",
                title="Emergency departments report respiratory symptom increase",
                url="https://example.com/respiratory-cluster",
                publisher="Public Health Monitor",
                credibility_score=0.76,
                published_at=now - timedelta(hours=5),
                raw_text="Hospitals reported a moderate rise in respiratory admissions across three neighborhoods.",
            )
        ],
        timeline=[
            TimelineEvent(
                timestamp=now - timedelta(days=1, hours=3),
                label="Cluster signal",
                description="Emergency department notes showed a respiratory complaint increase.",
            )
        ],
        recommended_actions=[
            "Increase testing coverage for affected neighborhoods.",
            "Monitor ICU occupancy and oxygen demand daily.",
            "Publish public guidance once laboratory confirmation improves.",
        ],
        risk_explanation=RiskExplanation(
            confidence=0.66,
            drivers=["Admission increase", "Limited lab confirmation", "Moderate geographic concentration"],
            feature_importance={"admission_growth": 0.38, "lab_confidence": 0.19, "spread_signal": 0.18, "source_volume": 0.12},
        ),
    ),
]

