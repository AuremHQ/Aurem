"""
services/video_pipeline/house_rules.py — 2026-09-10, admin-editable
"house rules" for ORA Chat video generation.

Single current-doc store (no version history — unlike the ORA Chat
house_rules.py this mirrors, video house rules are a simple pass/fail
gate on a short script, not a running personality/tone the model
must hold across a whole conversation). Empty `rules_text` = no
enforcement at all (create_job never even calls the LLM check).
"""
from __future__ import annotations

import time
from typing import Optional

_DOC_ID = "video_house_rules"
VALID_MODES = {"block", "rewrite"}


async def get_rules(db) -> dict:
    doc = await db.admin_settings.find_one({"_id": _DOC_ID}) or {}
    return {
        "rules_text": doc.get("rules_text", ""),
        "enforcement_mode": doc.get("enforcement_mode", "block"),
    }


async def set_rules(db, *, rules_text: Optional[str] = None,
                      enforcement_mode: Optional[str] = None,
                      updated_by: Optional[str] = None) -> dict:
    updates: dict = {"updated_at": time.time(), "updated_by": updated_by}
    if rules_text is not None:
        updates["rules_text"] = rules_text.strip()[:4000]
    if enforcement_mode is not None:
        if enforcement_mode not in VALID_MODES:
            raise ValueError(f"enforcement_mode must be one of {VALID_MODES}")
        updates["enforcement_mode"] = enforcement_mode
    await db.admin_settings.update_one({"_id": _DOC_ID}, {"$set": updates}, upsert=True)
    return await get_rules(db)
