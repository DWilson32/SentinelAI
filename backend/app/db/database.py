from collections.abc import Generator
from threading import Lock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


database_url = normalize_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}

engine = create_engine(database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_bootstrap_lock = Lock()
_bootstrapped = False


class Base(DeclarativeBase):
    pass


def is_postgres() -> bool:
    return database_url.startswith("postgresql")


def ensure_extensions() -> None:
    """pgvector must be installed before create_all builds the vector column."""
    if not is_postgres():
        return
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def create_db_tables() -> None:
    ensure_extensions()
    Base.metadata.create_all(bind=engine)


def migrate_db_tables() -> None:
    if not is_postgres():
        with engine.begin() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(incidents)"))}
            if columns and "geo_precision" not in columns:
                connection.execute(text("ALTER TABLE incidents ADD COLUMN geo_precision VARCHAR(16)"))
            if columns and "severity_note" not in columns:
                connection.execute(text("ALTER TABLE incidents ADD COLUMN severity_note TEXT"))
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS geo_precision VARCHAR(16)"))
        connection.execute(text("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS severity_note TEXT"))
        connection.execute(text("ALTER TABLE sources ALTER COLUMN url TYPE TEXT"))
        connection.execute(text("ALTER TABLE sources ALTER COLUMN raw_text TYPE TEXT"))
        # HNSW rather than IVFFlat: it needs no training pass over existing rows,
        # so it is valid from the first insert and holds recall as the table grows.
        # vector_cosine_ops matches the normalised bge embeddings.
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_source_chunks_embedding_hnsw "
                "ON source_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )


def bootstrap_database() -> None:
    global _bootstrapped

    if _bootstrapped:
        return

    with _bootstrap_lock:
        if _bootstrapped:
            return

        from app.db.seed import seed_initial_data

        create_db_tables()
        migrate_db_tables()
        db = SessionLocal()
        try:
            seed_initial_data(db)
            _bootstrapped = True
        finally:
            db.close()


def get_db() -> Generator[Session, None, None]:
    bootstrap_database()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
