from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import IncidentModel, SourceModel, TimelineEventModel
from app.services.seed_data import INCIDENTS


def seed_initial_data(db: Session) -> None:
    has_incidents = db.scalar(select(IncidentModel.id).limit(1))
    if has_incidents:
        return

    for incident in INCIDENTS:
        db_incident = IncidentModel(
            id=incident.id,
            title=incident.title,
            category=incident.category,
            location=incident.location,
            latitude=incident.latitude,
            longitude=incident.longitude,
            severity=incident.severity,
            risk_score=incident.risk_score,
            status=incident.status,
            summary=incident.summary,
            created_at=incident.created_at,
            updated_at=incident.updated_at,
            recommended_actions=incident.recommended_actions,
            risk_confidence=incident.risk_explanation.confidence,
            risk_drivers=incident.risk_explanation.drivers,
            feature_importance=incident.risk_explanation.feature_importance,
        )
        db_incident.sources = [
            SourceModel(
                id=source.id,
                title=source.title,
                url=source.url,
                publisher=source.publisher,
                credibility_score=source.credibility_score,
                published_at=source.published_at,
                raw_text=source.raw_text,
            )
            for source in incident.sources
        ]
        db_incident.timeline = [
            TimelineEventModel(
                timestamp=event.timestamp,
                label=event.label,
                description=event.description,
            )
            for event in incident.timeline
        ]
        db.add(db_incident)

    db.commit()
