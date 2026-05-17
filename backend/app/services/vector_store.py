from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings
from app.services.embedding_service import embedding_service

INDEX_METADATA_POINT_ID = "00000000-0000-0000-0000-000000000001"
SOURCE_CHUNK_RECORD_TYPE = "source_chunk"
INDEX_METADATA_RECORD_TYPE = "index_metadata"


class VectorStore:
    def __init__(self) -> None:
        self._client: QdrantClient | None = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            if settings.qdrant_url:
                self._client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
            else:
                self._client = QdrantClient(path=settings.qdrant_path)
        return self._client

    def ensure_collection(self) -> None:
        if self.client.collection_exists(settings.qdrant_collection):
            info = self.client.get_collection(settings.qdrant_collection)
            current_size = info.config.params.vectors.size  # type: ignore[union-attr]
            if current_size != embedding_service.vector_size:
                self.client.delete_collection(settings.qdrant_collection)
            else:
                return
        self.client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=qmodels.VectorParams(
                size=embedding_service.vector_size,
                distance=qmodels.Distance.COSINE,
            ),
        )

    def count(self) -> int:
        return self.count_chunks()

    def count_chunks(self) -> int:
        if not self.client.collection_exists(settings.qdrant_collection):
            return 0
        return self.client.count(
            collection_name=settings.qdrant_collection,
            count_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="record_type",
                        match=qmodels.MatchValue(value=SOURCE_CHUNK_RECORD_TYPE),
                    )
                ]
            ),
            exact=True,
        ).count

    def reset_collection(self) -> None:
        if self.client.collection_exists(settings.qdrant_collection):
            self.client.delete_collection(settings.qdrant_collection)

    def upsert(self, points: list[qmodels.PointStruct]) -> None:
        if not points:
            return
        self.ensure_collection()
        self.client.upsert(collection_name=settings.qdrant_collection, points=points)

    def get_index_metadata(self) -> dict | None:
        if not self.client.collection_exists(settings.qdrant_collection):
            return None
        records = self.client.retrieve(
            collection_name=settings.qdrant_collection,
            ids=[INDEX_METADATA_POINT_ID],
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return None
        payload = records[0].payload or {}
        if payload.get("record_type") != INDEX_METADATA_RECORD_TYPE:
            return None
        return payload

    def set_index_metadata(self, metadata: dict) -> None:
        self.ensure_collection()
        payload = {"record_type": INDEX_METADATA_RECORD_TYPE, **metadata}
        self.client.upsert(
            collection_name=settings.qdrant_collection,
            points=[
                qmodels.PointStruct(
                    id=INDEX_METADATA_POINT_ID,
                    vector=[0.0] * embedding_service.vector_size,
                    payload=payload,
                )
            ],
        )

    def search(
        self,
        query_vector: list[float],
        *,
        limit: int,
        category: str | None = None,
        severity: str | None = None,
    ) -> list[qmodels.ScoredPoint]:
        if not self.client.collection_exists(settings.qdrant_collection):
            return []
        filters: list[qmodels.FieldCondition] = [
            qmodels.FieldCondition(
                key="record_type",
                match=qmodels.MatchValue(value=SOURCE_CHUNK_RECORD_TYPE),
            )
        ]
        if category:
            filters.append(
                qmodels.FieldCondition(key="category", match=qmodels.MatchValue(value=category))
            )
        if severity:
            filters.append(
                qmodels.FieldCondition(key="severity", match=qmodels.MatchValue(value=severity))
            )
        query_filter = qmodels.Filter(must=filters) if filters else None
        return self.client.search(
            collection_name=settings.qdrant_collection,
            query_vector=query_vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )


vector_store = VectorStore()
