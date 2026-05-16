from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import IncidentModel
from app.schemas.incident import Incident, IncidentDetail, RiskExplanation, Source, TimelineEvent


class IncidentService:
    def list_incidents(self, db: Session) -> list[Incident]:
        incidents = db.scalars(select(IncidentModel).order_by(IncidentModel.risk_score.desc())).all()
        return [self._to_incident(incident) for incident in incidents]

    def get_incident(self, db: Session, incident_id: str) -> IncidentDetail | None:
        incident = db.scalar(
            select(IncidentModel)
            .where(IncidentModel.id == incident_id)
            .options(joinedload(IncidentModel.sources), joinedload(IncidentModel.timeline))
        )
        if incident is None:
            return None
        return self._to_detail(incident)

    def list_incident_details(self, db: Session) -> list[IncidentDetail]:
        incidents = (
            db.scalars(
                select(IncidentModel)
                .options(joinedload(IncidentModel.sources), joinedload(IncidentModel.timeline))
                .order_by(IncidentModel.risk_score.desc())
            )
            .unique()
            .all()
        )
        return [self._to_detail(incident) for incident in incidents]

    def _to_incident(self, incident: IncidentModel) -> Incident:
        return Incident(
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
        )

    def _to_detail(self, incident: IncidentModel) -> IncidentDetail:
        return IncidentDetail(
            **self._to_incident(incident).model_dump(),
            sources=[
                Source(
                    id=source.id,
                    title=source.title,
                    url=source.url,
                    publisher=source.publisher,
                    credibility_score=source.credibility_score,
                    published_at=source.published_at,
                    raw_text=source.raw_text,
                )
                for source in sorted(incident.sources, key=lambda source: source.published_at, reverse=True)
            ],
            timeline=[
                TimelineEvent(timestamp=event.timestamp, label=event.label, description=event.description)
                for event in sorted(incident.timeline, key=lambda event: event.timestamp)
            ],
            recommended_actions=incident.recommended_actions,
            risk_explanation=RiskExplanation(
                confidence=incident.risk_confidence,
                drivers=incident.risk_drivers,
                feature_importance=incident.feature_importance,
            ),
        )


incident_service = IncidentService()
