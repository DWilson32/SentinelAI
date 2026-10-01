"""Download the embedding model into the project at build time.

Render's free tier discards files written at runtime whenever the instance spins
down, so a model fetched lazily on first use would be re-downloaded from
Hugging Face on every cold start (~40 s, and rate-limited without a token).
Running this during the build bakes the files into the deploy image instead,
cutting first-use load to well under a second.

    python scripts/prefetch_model.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.embedding_service import embedding_service, model_cache_dir  # noqa: E402

started = time.time()
vector = embedding_service.embed(["SentinelAI build-time model warm-up"])[0]
print(
    f"Prefetched {settings.embedding_model} ({len(vector)} dims) into "
    f"{model_cache_dir()} in {time.time() - started:.1f}s"
)
