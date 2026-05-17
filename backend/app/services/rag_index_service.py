import hashlib
import uuid
from dataclasses import dataclass

from qdrant_client.http import models as qmodels
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.db.models import IncidentModel, SourceModel
from app.services.embedding_service import embedding_service
from app.services.vector_store import SOURCE_CHUNK_RECORD_TYPE, vector_store


@dataclass(frozen=True)
class SourceChunk:
    text: str
    metadata: dict[str, str | int | float]


class RagIndexService:
    def sync_all(self, db: Session, *, force: bool = False) -> int:
        if force:
            vector_store.reset_collection()

        incidents = (
            db.scalars(
                select(IncidentModel).options(joinedload(IncidentModel.sources)).order_by(IncidentModel.id)
            )
            .unique()
            .all()
        )
        chunks = self._build_chunks(incidents)
        if not chunks:
            return 0

        metadata = self._index_metadata(chunks)
        if not force and self._is_current(metadata):
            return int(metadata["chunk_count"])

        points = self._points_from_chunks(chunks)
        vector_store.ensure_collection()
        if points:
            vector_store.upsert(points)
            vector_store.set_index_metadata(metadata)
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
        points = self._points_from_chunks(self._build_chunks(incidents))
        vector_store.upsert(points)
        return len(points)

    def _build_chunks(self, incidents: list[IncidentModel]) -> list[SourceChunk]:
        chunks: list[SourceChunk] = []
        for incident in incidents:
            for source in incident.sources:
                source_chunks = self._chunk_text(self._document_text(incident, source))
                total_chunks = len(source_chunks)
                for chunk_index, chunk_text in enumerate(source_chunks):
                    chunks.append(
                        SourceChunk(
                            text=chunk_text,
                            metadata={
                                "record_type": SOURCE_CHUNK_RECORD_TYPE,
                                "source_id": source.id,
                                "chunk_id": f"{source.id}:{chunk_index}",
                                "chunk_index": chunk_index,
                                "chunk_count": total_chunks,
                                "incident_id": incident.id,
                                "incident_title": incident.title,
                                "category": incident.category,
                                "severity": incident.severity,
                                "location": incident.location,
                                "risk_score": incident.risk_score,
                                "source_title": source.title,
                                "publisher": source.publisher,
                                "url": source.url,
                                "snippet": chunk_text[:700],
                            },
                        )
                    )
        return chunks

    def _points_from_chunks(self, chunks: list[SourceChunk]) -> list[qmodels.PointStruct]:
        if not chunks:
            return []

        vectors = embedding_service.embed([chunk.text for chunk in chunks])
        return [
            qmodels.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, str(chunk.metadata["chunk_id"]))),
                vector=vector,
                payload=chunk.metadata,
            )
            for vector, chunk in zip(vectors, chunks, strict=True)
        ]

    def _chunk_text(self, text: str) -> list[str]:
        compact = " ".join(text.split())
        if not compact:
            return []

        chunk_size = max(300, settings.rag_chunk_chars)
        overlap = max(0, min(settings.rag_chunk_overlap_chars, chunk_size // 2))
        chunks: list[str] = []
        start = 0
        while start < len(compact):
            end = min(start + chunk_size, len(compact))
            if end < len(compact):
                boundary = compact.rfind(" ", start + chunk_size // 2, end)
                if boundary > start:
                    end = boundary
            chunks.append(compact[start:end].strip())
            if end >= len(compact):
                break
            start = max(0, end - overlap)
        return chunks

    def _index_metadata(self, chunks: list[SourceChunk]) -> dict[str, str | int]:
        embedding_model = settings.openai_embedding_model if settings.use_openai_embeddings else settings.embedding_model
        embedding_provider = "openai" if settings.openai_api_key and settings.use_openai_embeddings else "fastembed"
        hash_input = {
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "embedding_dimensions": embedding_service.vector_size,
            "chunk_chars": settings.rag_chunk_chars,
            "chunk_overlap_chars": settings.rag_chunk_overlap_chars,
            "chunks": [
                {
                    "chunk_id": chunk.metadata["chunk_id"],
                    "incident_id": chunk.metadata["incident_id"],
                    "source_id": chunk.metadata["source_id"],
                    "text_sha256": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                }
                for chunk in chunks
            ],
        }
        fingerprint = hashlib.sha256(repr(hash_input).encode("utf-8")).hexdigest()
        return {
            "fingerprint": fingerprint,
            "chunk_count": len(chunks),
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "embedding_dimensions": embedding_service.vector_size,
            "chunk_chars": settings.rag_chunk_chars,
            "chunk_overlap_chars": settings.rag_chunk_overlap_chars,
        }

    def _is_current(self, metadata: dict[str, str | int]) -> bool:
        stored = vector_store.get_index_metadata()
        if not stored:
            return False
        return (
            stored.get("fingerprint") == metadata["fingerprint"]
            and int(stored.get("chunk_count", -1)) == metadata["chunk_count"]
            and vector_store.count_chunks() == metadata["chunk_count"]
        )

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
