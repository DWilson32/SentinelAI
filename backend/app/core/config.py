import json

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SentinelAI"
    allowed_origins_value: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias="ALLOWED_ORIGINS",
    )

    @property
    def allowed_origins(self) -> list[str]:
        stripped = self.allowed_origins_value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if not isinstance(parsed, list):
                raise ValueError("ALLOWED_ORIGINS JSON value must be a list")
            return [str(origin).strip() for origin in parsed if str(origin).strip()]
        return [origin.strip() for origin in stripped.split(",") if origin.strip()]

    database_url: str = "sqlite:///./sentinel.db"
    gnews_api_key: str | None = None
    news_api_key: str | None = None
    # ReliefWeb only serves pre-approved app names; request one at
    # https://apidoc.reliefweb.int/parameters#appname. Unset means the feed is
    # skipped and shown as disabled, rather than failing with 403 on every sync.
    reliefweb_appname: str | None = None

    # Shared secret guarding ingestion and reindex endpoints. Unset means those
    # endpoints are disabled rather than public.
    sentinel_admin_key: str | None = None
    admin_key_env: str = "SENTINEL_ADMIN_KEY"

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimensions: int = 384
    # Model files are downloaded here at build time (scripts/prefetch_model.py) so
    # they are baked into the deploy image. Render's free tier discards files written
    # at runtime on every spin-down, which would mean re-downloading on each cold start.
    embedding_cache_dir: str = "./.model_cache"
    use_openai_embeddings: bool = False
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"

    rag_top_k: int = 4
    # Vector search always returns its k nearest neighbours, however unrelated.
    # Chunks below this cosine similarity are discarded so an off-topic question
    # gets "nothing found" rather than four irrelevant citations.
    # Calibrated on live data (Oct 2026, bge-small-en-v1.5, 160 chunks): 8 on-topic
    # queries scored 0.638-0.775 top-1; 8 off-topic scored 0.455-0.666. 0.60 kept
    # all on-topic and rejected 7/8 off-topic; the one leak ("stock market prices")
    # matched a genuine business story in the feed. Revisit with a labelled eval set.
    rag_min_similarity: float = 0.60
    rag_chunk_chars: int = 900
    rag_chunk_overlap_chars: int = 150
    vector_rag_enabled: bool = True
    index_ingested_sources: bool = True

    # extra="ignore": older .env files still carry QDRANT_* keys from before the
    # pgvector migration; with the default "forbid" those would crash startup.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
