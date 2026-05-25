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

    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_path: str = "./qdrant_data"
    qdrant_collection: str = "sentinel_sources"

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimensions: int = 384
    use_openai_embeddings: bool = False
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"

    rag_top_k: int = 4
    rag_chunk_chars: int = 900
    rag_chunk_overlap_chars: int = 150
    vector_rag_enabled: bool = False
    index_ingested_sources: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
