"""
services/video_pipeline/secrets_store.py — 2026-09-10, admin self-serve
secret keys for the ORA Chat "Video generation" mode.

- Deepgram: always required (TTS has no Universal Key path).
- fal.ai: OPTIONAL BYOK. If left blank, the pipeline automatically
  falls back to the Emergent Universal Key (EMERGENT_LLM_KEY, already
  configured for the whole app) — nothing to paste for lip-sync
  unless the admin wants to bill their own Fal account instead.

Same DB-override pattern as services/llm_config_store.py, whose Fernet
encryption helpers are reused here — keys never touch backend/.env or
git; the admin pastes them once in Settings → Video generation keys
and services/video_pipeline/pipeline.py reads them straight from
Mongo.
"""
from __future__ import annotations

import time
from typing import Optional

from services.llm_config_store import (
    EncryptionNotConfigured,
    decrypt_key,
    encrypt_key,
    key_hint,
)

_DOC_ID = "video_pipeline_secrets"


async def get_secrets_public(db) -> dict:
    """Secret-free — safe to return directly from an API response."""
    doc = await db.admin_settings.find_one({"_id": _DOC_ID}) or {}
    return {
        "deepgram_configured": bool(doc.get("deepgram_api_key_enc")),
        "deepgram_key_hint": doc.get("deepgram_key_hint"),
        "fal_configured": bool(doc.get("fal_api_key_enc")),
        "fal_key_hint": doc.get("fal_key_hint"),
    }


async def get_secrets(db) -> dict:
    """Decrypted values for internal pipeline use ONLY — never return
    this dict from an API response. `fal_api_key` is None when the
    admin hasn't set one, which run_lipsync() treats as "use the
    Universal Key fallback"."""
    doc = await db.admin_settings.find_one({"_id": _DOC_ID}) or {}
    result = {"deepgram_api_key": None, "fal_api_key": None}
    try:
        if doc.get("deepgram_api_key_enc"):
            result["deepgram_api_key"] = decrypt_key(doc["deepgram_api_key_enc"])
        if doc.get("fal_api_key_enc"):
            result["fal_api_key"] = decrypt_key(doc["fal_api_key_enc"])
    except EncryptionNotConfigured:
        pass
    return result


async def set_secrets(db, *, deepgram_api_key: Optional[str] = None,
                        fal_api_key: Optional[str] = None,
                        updated_by: Optional[str] = None) -> dict:
    updates: dict = {"updated_at": time.time(), "updated_by": updated_by}
    # Empty/None = keep current value — a blank edit never wipes a
    # working key (same rule as llm_config_store.update_config).
    if deepgram_api_key:
        updates["deepgram_api_key_enc"] = encrypt_key(deepgram_api_key)
        updates["deepgram_key_hint"] = key_hint(deepgram_api_key)
    if fal_api_key:
        updates["fal_api_key_enc"] = encrypt_key(fal_api_key)
        updates["fal_key_hint"] = key_hint(fal_api_key)
    await db.admin_settings.update_one({"_id": _DOC_ID}, {"$set": updates}, upsert=True)
    return await get_secrets_public(db)


async def clear_fal_key(db, *, updated_by: Optional[str] = None) -> dict:
    """Switch back to the Emergent Universal Key fallback for lip-sync."""
    await db.admin_settings.update_one(
        {"_id": _DOC_ID},
        {"$unset": {"fal_api_key_enc": "", "fal_key_hint": ""},
         "$set": {"updated_at": time.time(), "updated_by": updated_by}},
    )
    return await get_secrets_public(db)
