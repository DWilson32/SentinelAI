import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from qdrant_client.http import models as qmodels

from app.db.models import IncidentModel, SourceModel
from app.services.embedding_service import embedding_service
from app.services.vector_store import vector_store


class RagIndexService:
    def sync_all(self, db: Session, *, force: bool = False) -> int:
        source_count = db.scalar(select(func.count()).select_from(SourceModel)) or 0
        if source_count == 0:
            return 0
        if force:
            vector_store.reset_collection()
        elif vector_store.count() == source_count:
            return source_count

        incidents = (
            db.scalars(
                select(IncidentModel).options(joinedload(IncidentModel.sources)).order_by(IncidentModel.id)
            )
            .unique()
            .all()
        )
        points = self._build_points(incidents)
        vector_store.ensure_collection()
        if points:
            vector_store.upsert(points)
        return len(points)

    def index_incidents(self, db: Session, incident_ids: list[str]) -> int:
        if not incident_ids:
            return 0
        incidents = (
            db.scalars(
                select(IncidentModel)
                .where(IncidentModel.id.in_(incident_ids))
                .options(joinedload(IncidentModel.sources))
            )
            .unique()
            .all()
        )
        points = self._build_points(incidents)
        vector_store.upsert(points)
        return len(points)

    def _build_points(self, incidents: list[IncidentModel]) -> list[qmodels.PointStruct]:
        documents: list[str] = []
        metadata: list[dict[str, str | float]] = []

        for incident in incidents:
            for source in incident.sources:
                documents.append(self._document_text(incident, source))
                metadata.append(
                    {
                        "source_id": source.id,
                        "incident_id": incident.id,
                        "incident_title": incident.title,
                        "category": incident.category,
                        "severity": incident.severity,
                        "location": incident.location,
                        "risk_score": incident.risk_score,
                        "source_title": source.title,
                        "publisher": source.publisher,
                        "url": source.url,
                        "snippet": source.raw_text[:500],
                    }
                )

        if not documents:
            return []

        vectors = embedding_service.embed(documents)
        return [
            qmodels.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, str(meta["source_id"]))),
                vector=vector,
                payload=meta,
            )
            for vector, meta in zip(vectors, metadata, strict=True)
        ]

    def _document_text(self, incident: IncidentModel, source: SourceModel) -> str:
        return (
            f"Title: {incident.title}\n"
            f"Category: {incident.category}\n"
            f"Location: {incident.location}\n"
            f"Severity: {incident.severity}\n"
            f"Risk score: {incident.risk_score}\n"
            f"Summary: {incident.summary}\n"
            f"Source: {source.title}\n"
            f"Publisher: {source.publisher}\n"
            f"Content: {source.raw_text}"
        )


rag_index_service = RagIndexService()
