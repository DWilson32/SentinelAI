import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.db.models import IncidentModel, SourceModel
from app.services.embedding_service import embedding_service
from app.services.vector_store import vector_store

# Chunks written to the index per round. Memory while embedding is bounded by
# settings.embedding_batch_size instead, inside the embedding service.
EMBED_BATCH_SIZE = 64


@dataclass(frozen=True)
class SourceChunk:
    id: str
    incident_id: str
    source_id: str
    chunk_index: int
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SyncResult:
    embedded: int
    unchanged: int
    removed: int

    @property
    def total(self) -> int:
        return self.embedded + self.unchanged


class RagIndexService:
    def sync_all(self, db: Session, *, force: bool = False) -> SyncResult:
        """Bring the chunk table in line with the current incidents.

        Incremental: a chunk is re-embedded only when its text or the embedding
        model changed, and chunks whose source no longer exists are removed.
        force=True re-embeds everything, e.g. after changing the chunking rules.
        """
        if not vector_store.available:
            return SyncResult(embedded=0, unchanged=0, removed=0)

        incidents = (
            db.scalars(select(IncidentModel).options(joinedload(IncidentModel.sources)))
            .unique()
            .all()
        )
        chunks = self._build_chunks(incidents)
        model = self._model_id()
        existing = vector_store.fingerprints(db)

        to_embed = [c for c in chunks if force or existing.get(c.id) != (c.sha256, model)]
        removed = vector_store.delete(db, set(existing) - {c.id for c in chunks})
        self._embed_and_store(db, to_embed, model)
        db.commit()

        return SyncResult(
            embedded=len(to_embed),
            unchanged=len(chunks) - len(to_embed),
            removed=removed,
        )

    def index_incidents(self, db: Session, incident_ids: list[str]) -> int:
        """Embed the given incidents' new or changed chunks — called after an
        ingest creates incidents or adds reports to existing ones."""
        if not vector_store.available or not incident_ids:
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
        model = self._model_id()
        existing = vector_store.fingerprints(db)
        chunks = [c for c in self._build_chunks(incidents) if existing.get(c.id) != (c.sha256, model)]
        self._embed_and_store(db, chunks, model)
        db.commit()
        return len(chunks)

    def first_chunk(self, text: str) -> str:
        """The chunk a document is matched on as a whole; see entity_resolution."""
        chunks = self._chunk_text(text)
        return chunks[0] if chunks else ""

    def _embed_and_store(self, db: Session, chunks: list[SourceChunk], model: str) -> None:
        indexed_at = datetime.now(timezone.utc)
        for start in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[start : start + EMBED_BATCH_SIZE]
            vectors = embedding_service.embed([chunk.text for chunk in batch])
            vector_store.upsert(
                db,
                [
                    {
                        "id": chunk.id,
                        "incident_id": chunk.incident_id,
                        "source_id": chunk.source_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.text,
                        "content_sha256": chunk.sha256,
                        "embedding_model": model,
                        "embedding": vector,
                        "indexed_at": indexed_at,
                    }
                    for chunk, vector in zip(batch, vectors, strict=True)
                ],
            )

    def _model_id(self) -> str:
        if settings.openai_api_key and settings.use_openai_embeddings:
            return f"openai:{settings.openai_embedding_model}"
        return f"fastembed:{settings.embedding_model}"

    def _build_chunks(self, incidents: list[IncidentModel]) -> list[SourceChunk]:
        chunks: list[SourceChunk] = []
        for incident in incidents:
            for source in incident.sources:
                for chunk_index, text in enumerate(self._chunk_text(self._document_text(incident, source))):
                    chunks.append(
                        SourceChunk(
                            id=f"{source.id}:{chunk_index}",
                            incident_id=incident.id,
                            source_id=source.id,
                            chunk_index=chunk_index,
                            text=text,
                        )
                    )
        return chunks

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

    def _document_text(self, incident: IncidentModel, source: SourceModel) -> str:
        return document_text(
            title=incident.title,
            category=incident.category,
            location=incident.location,
            summary=incident.summary,
            source_title=source.title,
            publisher=source.publisher,
            content=source.raw_text,
        )


def document_text(
    *, title: str, category: str, location: str, summary: str, source_title: str, publisher: str, content: str
) -> str:
    # Severity and risk score are deliberately left out. They are joined in
    # live at query time, and embedding them would both add noise to semantic
    # matching and force a re-embed every time an incident is re-scored.
    return (
        f"Title: {title}\n"
        f"Category: {category}\n"
        f"Location: {location}\n"
        f"Summary: {summary}\n"
        f"Source: {source_title}\n"
        f"Publisher: {publisher}\n"
        f"Content: {content}"
    )


rag_index_service = RagIndexService()
