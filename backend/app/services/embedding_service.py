from functools import lru_cache

from fastembed import TextEmbedding

from app.core.config import settings


@lru_cache(maxsize=1)
def _local_model() -> TextEmbedding:
    return TextEmbedding(model_name=settings.embedding_model)


class EmbeddingService:
    @property
    def vector_size(self) -> int:
        return settings.embedding_dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if settings.openai_api_key and settings.use_openai_embeddings:
            return self._embed_openai(texts)
        return [vector.tolist() for vector in _local_model().embed(texts)]

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.embeddings.create(model=settings.openai_embedding_model, input=texts)
        return [item.embedding for item in response.data]


embedding_service = EmbeddingService()
