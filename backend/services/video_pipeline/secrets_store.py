"""
services/video_pipeline/secrets_store.py — 2026-09-10, admin self-serve
secret keys for the ORA Chat "Video generation" mode (Deepgram Aura-2
TTS + RunPod MuseTalk lip-sync pipeline).

Same DB-override pattern as services/llm_config_store.py, whose Fernet
encryption helpers are reused here — secrets never touch backend/.env
or git; the admin pastes them once in Settings → Video generation keys
and services/video_pipeline/pipeline.py reads them straight from Mongo.
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
        "runpod_configured": bool(doc.get("runpod_api_key_enc")),
        "runpod_key_hint": doc.get("runpod_key_hint"),
        "runpod_endpoint_id": doc.get("runpod_endpoint_id"),
    }


async def get_secrets(db) -> dict:
    """Decrypted values for internal pipeline use ONLY — never return
    this dict from an API response."""
    doc = await db.admin_settings.find_one({"_id": _DOC_ID}) or {}
    result = {
        "deepgram_api_key": None,
        "runpod_api_key": None,
        "runpod_endpoint_id": doc.get("runpod_endpoint_id"),
    }
    try:
        if doc.get("deepgram_api_key_enc"):
            result["deepgram_api_key"] = decrypt_key(doc["deepgram_api_key_enc"])
        if doc.get("runpod_api_key_enc"):
            result["runpod_api_key"] = decrypt_key(doc["runpod_api_key_enc"])
    except EncryptionNotConfigured:
        pass
    return result


async def set_secrets(
    db,
    *,
    deepgram_api_key: Optional[str] = None,
    runpod_api_key: Optional[str] = None,
    runpod_endpoint_id: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> dict:
    updates: dict = {"updated_at": time.time(), "updated_by": updated_by}
    # Empty/None = keep current value — a blank edit never wipes a
    # working key (same rule as llm_config_store.update_config).
    if deepgram_api_key:
        updates["deepgram_api_key_enc"] = encrypt_key(deepgram_api_key)
        updates["deepgram_key_hint"] = key_hint(deepgram_api_key)
    if runpod_api_key:
        updates["runpod_api_key_enc"] = encrypt_key(runpod_api_key)
        updates["runpod_key_hint"] = key_hint(runpod_api_key)
    if runpod_endpoint_id is not None and runpod_endpoint_id.strip():
        updates["runpod_endpoint_id"] = runpod_endpoint_id.strip()
    await db.admin_settings.update_one({"_id": _DOC_ID}, {"$set": updates}, upsert=True)
    return await get_secrets_public(db)
