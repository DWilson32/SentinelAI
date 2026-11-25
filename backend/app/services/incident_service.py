from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import IncidentAliasModel, IncidentModel, SourceModel
from app.services.lifecycle import CATEGORY_WINDOWS, DEFAULT_WINDOW, as_utc, is_active
from app.schemas.incident import Incident, IncidentDetail, RiskExplanation, Source, TimelineEvent


class IncidentService:
    def active_ids(self, db: Session) -> list[str]:
        """Ids of the incidents still being reported, by the dashboard's own window.

        The follow-up work and alerting both need this, and both would otherwise
        act on the archive: severity is never retired when reporting stops, so
        most of the severe incidents on file are months of history.
        """
        rows = db.execute(
            select(IncidentModel.id, IncidentModel.category, func.max(SourceModel.published_at))
            .join(SourceModel, SourceModel.incident_id == IncidentModel.id)
            .group_by(IncidentModel.id, IncidentModel.category)
        ).all()
        return [row[0] for row in rows if is_active(row[1], row[2])]

    def list_incidents(self, db: Session, *, active_only: bool = False) -> list[Incident]:
        """Incidents, active first, then by risk.

        active_only keeps the ones still being reported. The dashboard asks for
        that: by late November it was downloading all 1,602 stored incidents
        (1.3 MB) on every load to show the 65 that were active.
        """
        # Newest source and source count per incident in one grouped subquery,
        # rather than loading every source or querying once per incident.
        latest = (
            select(
                SourceModel.incident_id,
                func.max(SourceModel.published_at).label("last_published"),
                func.count(SourceModel.id).label("source_count"),
            )
            .group_by(SourceModel.incident_id)
            .subquery()
        )
        query = (
            select(IncidentModel, latest.c.last_published, latest.c.source_count)
            .outerjoin(latest, latest.c.incident_id == IncidentModel.id)
            .order_by(IncidentModel.risk_score.desc())
        )
        if active_only:
            # The widest activity window, so the database drops the archive and
            # is_active below applies each category's own window to the rest.
            widest = max([DEFAULT_WINDOW, *CATEGORY_WINDOWS.values()])
            query = query.where(latest.c.last_published >= datetime.now(timezone.utc) - widest)
        rows = db.execute(query).all()
        incidents = [
            self._to_incident(incident, last_published, source_count or 0)
            for incident, last_published, source_count in rows
        ]
        if active_only:
            incidents = [incident for incident in incidents if incident.active]
        # Active first, then by risk, so current events lead the list.
        return sorted(incidents, key=lambda i: (not i.active, -i.risk_score))

    def resolve_id(self, db: Session, incident_id: str) -> str:
        """The current id for incident_id, following a merge if there was one."""
        alias = db.get(IncidentAliasModel, incident_id)
        return alias.incident_id if alias else incident_id

    def get_incident(self, db: Session, incident_id: str) -> IncidentDetail | None:
        """The incident, or the one it was merged into; check the returned id."""
        incident = db.scalar(
            select(IncidentModel)
            .where(IncidentModel.id == self.resolve_id(db, incident_id))
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

    def _to_incident(self, incident: IncidentModel, last_published=None, source_count: int = 1) -> Incident:
        last_activity = as_utc(last_published or incident.created_at)
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
            last_activity_at=last_activity,
            active=is_active(incident.category, last_activity),
            source_count=source_count,
            geo_precision=incident.geo_precision,
            severity_note=incident.severity_note,
            evidence=incident.evidence,
        )

    def _to_detail(self, incident: IncidentModel) -> IncidentDetail:
        return IncidentDetail(
            **self._to_incident(
                incident,
                max((src.published_at for src in incident.sources), default=None),
                len(incident.sources),
            ).model_dump(),
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
