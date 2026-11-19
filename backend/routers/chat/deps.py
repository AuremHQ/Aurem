"""Shared FastAPI dependencies for the chat routers (N6)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Header, HTTPException

from cto_services.auth import current_dev
from cto_services.db import get_db


async def get_current_user_db(
    authorization: Optional[str] = Header(None),
) -> tuple[dict, Any]:
    """Resolve the dev user and the database handle for a chat route.

    Raises 503 when the database is not connected, exactly as the
    /turn/shipped and /turn/ship-cancelled routes did inline before.
    """
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    return user, db
