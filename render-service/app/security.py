"""Simple API-key auth for internal use only."""
from fastapi import Header, HTTPException, status

from .config import settings


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not x_api_key or x_api_key != settings.render_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )
