import hashlib
import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import router
from app.core.config import settings
from app.db.database import engine

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


def _database_reachable() -> tuple[bool, str | None]:
    """Cheapest possible round trip to the database.

    Deliberately does not go through get_db(), which would bootstrap tables and
    seed on every probe.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 - any failure means unreachable
        return False, f"{type(exc).__name__}: {exc}"[:200]


@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """A hash of the app's own source files, in a fixed order.

    Render once reported a deploy live while the previous build kept serving.
    The commit it passes in describes the deploy, not the files on disk, so
    /health reports this too: compare it with the same hash of the pushed commit.
    """
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py"), key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


@app.get("/health")
def health_check() -> dict[str, str]:
    """Liveness probe. Render is configured to poll this endpoint.

    It reports database state but still returns 200 while the process itself is
    healthy: failing here would make Render treat the instance as dead and
    restart it in a loop, which does not bring an expired database back. Use
    /health/deep for alerting.
    """
    reachable, detail = _database_reachable()
    payload = {
        "status": "ok",
        "service": "sentinel-ai",
        "database": "up" if reachable else "down",
        "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:7] or "unknown",
        "code": code_fingerprint(),
    }
    if detail:
        payload["database_error"] = detail
    return payload


@app.get("/health/deep")
def health_check_deep(response: Response) -> dict[str, str]:
    """Readiness probe: fails when the app cannot actually serve data.

    Point uptime monitoring at this. The database has silently expired more
    than once while the liveness probe kept returning 200, so an endpoint that
    goes red on dependency loss is the thing that surfaces it.
    """
    reachable, detail = _database_reachable()
    if not reachable:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "degraded",
            "service": "sentinel-ai",
            "database": "down",
            "detail": detail or "database unreachable",
        }
    return {"status": "ok", "service": "sentinel-ai", "database": "up"}
