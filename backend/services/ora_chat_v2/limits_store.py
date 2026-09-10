"""
services/ora_chat_v2/limits_store.py — admin-editable overrides for
the Admin ORA Chat's hourly message rate limit and daily token cap.

2026-09-10 — founder-reported bug: the daily token cap
(services/ora_chat_v2/engine.py::_daily_cap_ok) was a HARDCODED env
var (ORA_CHAT_DAILY_TOKEN_CAP, default 200000) with no admin UI to
change it and no relation to the $ balance shown at the top of that
chat panel — so hitting "Stream error: daily_token_cap" while the $
balance still had plenty of room looked like a bug/inconsistency.
Same DB-override-wins-over-env-var pattern already used for LLM
provider configs (services/llm_config_store.py) and USD caps
(services/llm_usd_cap.py): a saved value here wins over the env var,
no restart needed; if nothing's been saved yet, the env var default
applies exactly as before — zero behavior change until an admin
actually edits it in Settings → Models & LLM.
"""
from __future__ import annotations

import os
import time
from typing import Optional

_DOC_ID = "ora_chat_limits"


async def get_limits(db) -> dict:
    """0 for either field means "unlimited" — an admin's own explicit
    choice via the Settings screen, not an automatic exemption."""
    limits = {
        "rate_limit_per_hour": int(os.getenv("ORA_CHAT_RATE_LIMIT_PER_HOUR", "20")),
        "daily_token_cap": int(os.getenv("ORA_CHAT_DAILY_TOKEN_CAP", "200000")),
    }
    if db is None:
        return limits
    try:
        doc = await db.admin_settings.find_one({"_id": _DOC_ID})
        if doc:
            if doc.get("rate_limit_per_hour") is not None:
                limits["rate_limit_per_hour"] = int(doc["rate_limit_per_hour"])
            if doc.get("daily_token_cap") is not None:
                limits["daily_token_cap"] = int(doc["daily_token_cap"])
    except Exception:                                          # noqa: BLE001
        pass
    return limits


async def set_limits(db, *, rate_limit_per_hour: Optional[int] = None,
                      daily_token_cap: Optional[int] = None,
                      updated_by: Optional[str] = None) -> dict:
    updates: dict = {"updated_at": time.time(), "updated_by": updated_by}
    if rate_limit_per_hour is not None:
        if rate_limit_per_hour < 0:
            raise ValueError("rate_limit_per_hour must be >= 0 (0 = unlimited)")
        updates["rate_limit_per_hour"] = int(rate_limit_per_hour)
    if daily_token_cap is not None:
        if daily_token_cap < 0:
            raise ValueError("daily_token_cap must be >= 0 (0 = unlimited)")
        updates["daily_token_cap"] = int(daily_token_cap)
    await db.admin_settings.update_one({"_id": _DOC_ID}, {"$set": updates}, upsert=True)
    return await get_limits(db)
