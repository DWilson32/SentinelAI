from functools import lru_cache
from pathlib import Path

from fastembed import TextEmbedding

from app.core.config import settings


BACKEND_DIR = Path(__file__).resolve().parents[2]


def model_cache_dir() -> str:
    """Absolute cache path, resolved against backend/ rather than the working
    directory, so the build step and the server agree on where the model is."""
    path = Path(settings.embedding_cache_dir)
    return str(path if path.is_absolute() else BACKEND_DIR / path)


@lru_cache(maxsize=1)
def _local_model() -> TextEmbedding:
    return TextEmbedding(
        model_name=settings.embedding_model,
        cache_dir=model_cache_dir(),
        enable_cpu_mem_arena=settings.embedding_memory_arena,
    )


class EmbeddingService:
    @property
    def vector_size(self) -> int:
        return settings.embedding_dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if settings.openai_api_key and settings.use_openai_embeddings:
            return self._embed_openai(texts)
        # fastembed batches 256 texts by default, which a feed sync can reach;
        # the batch size is what bounds memory (see settings.embedding_batch_size).
        return [vector.tolist() for vector in _local_model().embed(texts, batch_size=settings.embedding_batch_size)]

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        kwargs = {"dimensions": settings.embedding_dimensions} if settings.openai_embedding_model.startswith("text-embedding-3") else {}
        response = client.embeddings.create(model=settings.openai_embedding_model, input=texts, **kwargs)
        return [item.embedding for item in response.data]


embedding_service = EmbeddingService()
