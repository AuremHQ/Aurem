"""
services/visual_bugfix_storage.py — Emergent Managed Object Storage
wrapper for the Multimodal Visual Bug-Fix pipeline (Phase 1).

Founder-approved (2026-09-12): presigned URLs were requested, but the
actual Emergent Object Storage API has NO presigned-URL support — all
access goes through OUR backend (see routers/visual_bugfix.py). The
"presigned PUT" ask is satisfied functionally: the client still just
picks/pastes a file and it's stored; the client just never sees a raw
storage URL, only our backend's own `/visual-bugfix/{id}/image` route.

Founder also asked for a 14-day auto-TTL. The storage API has NO
delete/expire endpoint at all ("no delete API — implement soft-delete
in your DB" per the playbook) — raw bytes in the backing bucket are
NOT something we can ever delete from here. What we CAN and DO
enforce: a MongoDB TTL index on `visual_bug_reports.created_at`
(routers/visual_bugfix.py), so the DB reference — and therefore the
app's ability to ever serve/display the image again — auto-expires
after 14 days, exactly like the existing `scaffold_drafts` TTL pattern
(routers/scaffold.py). This is the honest, disclosed limit of what
"auto-TTL" can mean given the storage API's constraints.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

APP_NAME = "aurem"

_STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() \
    or "https://integrations.emergentagent.com"
STORAGE_URL = _STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"

_storage_key: str | None = None
_init_lock = asyncio.Lock()


async def init_storage(force: bool = False) -> str:
    """Mint (once) and cache the session-scoped storage key. Safe to
    call on every request — returns the cached key with no network
    call once initialised."""
    global _storage_key
    if _storage_key and not force:
        return _storage_key
    async with _init_lock:
        if _storage_key and not force:
            return _storage_key
        key = (os.environ.get("EMERGENT_LLM_KEY") or "").strip()
        if not key:
            raise RuntimeError("EMERGENT_LLM_KEY not set — cannot init object storage")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{STORAGE_URL}/init", json={"emergent_key": key})
            resp.raise_for_status()
            _storage_key = resp.json()["storage_key"]
        return _storage_key


async def put_object(path: str, data: bytes, content_type: str) -> dict:
    """Upload bytes to `{APP_NAME}/{path}`. Returns {"path", "size", "etag"}.
    Retries once with a freshly-minted key on a 404 (dead cached key —
    per the playbook's documented status-code behaviour)."""
    full_path = f"{APP_NAME}/{path}"
    key = await init_storage()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.put(
            f"{STORAGE_URL}/objects/{full_path}",
            headers={"X-Storage-Key": key, "Content-Type": content_type},
            content=data,
        )
        if resp.status_code == 404:
            key = await init_storage(force=True)
            resp = await client.put(
                f"{STORAGE_URL}/objects/{full_path}",
                headers={"X-Storage-Key": key, "Content-Type": content_type},
                content=data,
            )
        resp.raise_for_status()
        return resp.json()


async def get_object(path: str) -> tuple[bytes, str]:
    """Download bytes from `{APP_NAME}/{path}`. Returns (bytes, content_type)."""
    full_path = f"{APP_NAME}/{path}"
    key = await init_storage()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            f"{STORAGE_URL}/objects/{full_path}", headers={"X-Storage-Key": key},
        )
        if resp.status_code == 404:
            key2 = await init_storage(force=True)
            if key2 != key:
                resp = await client.get(
                    f"{STORAGE_URL}/objects/{full_path}", headers={"X-Storage-Key": key2},
                )
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


async def warm_up() -> None:
    """Best-effort init at app startup — logs and swallows failure so
    a storage-provider hiccup never blocks the whole app from booting
    (mirrors other optional-subsystem startup hooks in main.py)."""
    try:
        await init_storage()
        logger.info("[visual_bugfix_storage] object storage initialised")
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix_storage] init failed (will lazy-retry on first use): %r", e)
