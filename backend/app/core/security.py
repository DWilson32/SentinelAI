from secrets import compare_digest

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

API_KEY_HEADER = "X-API-Key"

api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def require_admin_key(api_key: str | None = Depends(api_key_scheme)) -> None:
    """Guard state-changing endpoints behind a shared admin key.

    Fails closed: when no key is configured the endpoint is unavailable rather
    than open, so a missing environment variable can never silently expose
    ingestion to the public internet.
    """
    expected = (settings.sentinel_admin_key or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Admin operations are disabled until {settings.admin_key_env} is configured.",
        )

    if not api_key or not compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"A valid {API_KEY_HEADER} header is required for this operation.",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )
