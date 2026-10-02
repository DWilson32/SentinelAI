"""The local embedding model runs in small batches that hand memory back (see config)."""

import numpy as np

from app.core.config import settings
from app.services import embedding_service as module


class FakeModel:
    options: dict = {}
    batch_size: int | None = None

    def __init__(self, **options):
        FakeModel.options = options

    def embed(self, texts, batch_size):
        FakeModel.batch_size = batch_size
        return [np.zeros(3) for _ in texts]


def test_embeddings_run_in_small_batches_without_a_memory_arena(monkeypatch):
    # 64 at a time added 1,028 MB and killed the 512 MB server; 2 at a time adds 32 MB.
    monkeypatch.setattr(module, "TextEmbedding", FakeModel)
    monkeypatch.setattr(settings, "use_openai_embeddings", False)
    module._local_model.cache_clear()
    try:
        vectors = module.embedding_service.embed(["one", "two", "three"])
    finally:
        module._local_model.cache_clear()

    assert len(vectors) == 3
    assert FakeModel.batch_size == settings.embedding_batch_size == 2
    assert FakeModel.options["enable_cpu_mem_arena"] is False
