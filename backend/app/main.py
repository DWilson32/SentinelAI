from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.db.database import SessionLocal, create_db_tables
from app.db.seed import seed_initial_data

app = FastAPI(
    title="SentinelAI API",
    description="Autonomous crisis intelligence platform API.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.on_event("startup")
def bootstrap_database() -> None:
    create_db_tables()
    db = SessionLocal()
    try:
        seed_initial_data(db)
    finally:
        db.close()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "sentinel-ai"}
