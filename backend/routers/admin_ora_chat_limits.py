"""
routers/admin_ora_chat_limits.py — admin-editable Admin ORA Chat
limits (2026-09-10, founder-reported bug fix).

  GET /admin/llm/ora-chat-limits
  PUT /admin/llm/ora-chat-limits

Sibling to routers/admin_llm_config.py's `/admin/llm/configs` CRUD —
kept as its own small file since this edits two standalone numbers
(services/ora_chat_v2/limits_store.py), not a provider-config
document, so it doesn't fit that router's CRUD shape.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import current_dev, require_admin_dep
from cto_services.db import get_db
from services.ora_chat_v2.limits_store import get_limits, set_limits

router = APIRouter(
    prefix="/admin/llm", tags=["Admin · LLM Settings"],
    dependencies=[Depends(require_admin_dep)],
)


class UpdateLimitsBody(BaseModel):
    rate_limit_per_hour: Optional[int] = Field(None, ge=0, le=100_000)
    daily_token_cap: Optional[int] = Field(None, ge=0, le=100_000_000)


@router.get("/ora-chat-limits")
async def read_ora_chat_limits() -> dict:
    db = get_db()
    return await get_limits(db)


@router.put("/ora-chat-limits")
async def update_ora_chat_limits(
    body: UpdateLimitsBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    if body.rate_limit_per_hour is None and body.daily_token_cap is None:
        raise HTTPException(400, "Provide at least one of rate_limit_per_hour or daily_token_cap")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    user = await current_dev(authorization)
    try:
        return await set_limits(
            db,
            rate_limit_per_hour=body.rate_limit_per_hour,
            daily_token_cap=body.daily_token_cap,
            updated_by=user.get("user_id"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
