"""pgvector-backed store for embedded source chunks.

Chunks live in Postgres beside the incidents they belong to, so there is one
datastore to run and keep alive instead of two. Search joins back to incidents
for category, severity and risk score, so results always reflect the current
values rather than whatever was copied in when the chunk was indexed.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import is_postgres
from app.db.models import IncidentModel, SourceChunkModel, SourceModel

_UPSERT_COLUMNS = (
    "incident_id",
    "source_id",
    "chunk_index",
    "content",
    "content_sha256",
    "embedding_model",
    "embedding",
    "indexed_at",
)


@dataclass(frozen=True)
class ChunkHit:
    incident_id: str
    similarity: float
    content: str
    incident_title: str
    category: str
    severity: str
    location: str
    risk_score: float
    source_title: str
    publisher: str
    url: str


class VectorStore:
    @property
    def available(self) -> bool:
        """Vector search needs Postgres with pgvector.

        SQLite (local development) has neither, so callers fall back to keyword
        retrieval there rather than failing.
        """
        return settings.vector_rag_enabled and is_postgres()

    def count(self, db: Session) -> int:
        if not self.available:
            return 0
        return db.scalar(select(func.count()).select_from(SourceChunkModel)) or 0

    def fingerprints(self, db: Session) -> dict[str, tuple[str, str]]:
        """chunk id -> (content hash, embedding model) for everything indexed."""
        rows = db.execute(
            select(
                SourceChunkModel.id,
                SourceChunkModel.content_sha256,
                SourceChunkModel.embedding_model,
            )
        ).all()
        return {row.id: (row.content_sha256, row.embedding_model) for row in rows}

    def upsert(self, db: Session, rows: list[dict]) -> None:
        if not rows:
            return
        stmt = pg_insert(SourceChunkModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SourceChunkModel.id],
            set_={column: stmt.excluded[column] for column in _UPSERT_COLUMNS},
        )
        db.execute(stmt)

    def delete(self, db: Session, chunk_ids: Iterable[str]) -> int:
        ids = list(chunk_ids)
        if not ids:
            return 0
        db.execute(delete(SourceChunkModel).where(SourceChunkModel.id.in_(ids)))
        return len(ids)

    def search(
        self,
        db: Session,
        query_vector: list[float],
        *,
        limit: int,
        category: str | None = None,
        severity: str | None = None,
        exclude_incident_id: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[ChunkHit]:
        distance = SourceChunkModel.embedding.cosine_distance(query_vector)
        stmt = (
            select(
                SourceChunkModel.incident_id,
                SourceChunkModel.content,
                IncidentModel.title.label("incident_title"),
                IncidentModel.category,
                IncidentModel.severity,
                IncidentModel.location,
                IncidentModel.risk_score,
                SourceModel.title.label("source_title"),
                SourceModel.publisher,
                SourceModel.url,
                distance.label("distance"),
            )
            .join(IncidentModel, IncidentModel.id == SourceChunkModel.incident_id)
            .join(SourceModel, SourceModel.id == SourceChunkModel.source_id)
            .order_by(distance)
            .limit(limit)
        )
        if category:
            stmt = stmt.where(IncidentModel.category == category)
        if severity:
            stmt = stmt.where(IncidentModel.severity == severity)
        if exclude_incident_id:
            stmt = stmt.where(SourceChunkModel.incident_id != exclude_incident_id)

        hits: list[ChunkHit] = []
        for row in db.execute(stmt).all():
            # cosine distance is 1 - cosine similarity
            similarity = 1.0 - float(row.distance)
            if similarity < min_similarity:
                continue
            hits.append(
                ChunkHit(
                    incident_id=row.incident_id,
                    similarity=similarity,
                    content=row.content,
                    incident_title=row.incident_title,
                    category=row.category,
                    severity=row.severity,
                    location=row.location,
                    risk_score=float(row.risk_score),
                    source_title=row.source_title,
                    publisher=row.publisher,
                    url=row.url,
                )
            )
        return hits


vector_store = VectorStore()
