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


def create_db_tables() -> None:
    Base.metadata.create_all(bind=engine)


def migrate_db_tables() -> None:
    if not database_url.startswith("postgresql"):
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE sources ALTER COLUMN url TYPE TEXT"))
        connection.execute(text("ALTER TABLE sources ALTER COLUMN raw_text TYPE TEXT"))


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
