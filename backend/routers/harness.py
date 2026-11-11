"""
routers/harness.py — Aurem Loop harness, Phase A.

Builds `POST /api/harness/verify` (external client contract — see
below) and `GET /api/harness/page-status` / `GET /api/harness/content`
/ `GET /api/harness/release-status` / `POST /api/harness/waitlist`
(public page-content + Phase B readiness delivery, 2026-11-10/11). No
billing, no telemetry ingestion, no cloud-run endpoint — those are
separate, later phases and are deliberately not touched here.

Mounted with a bare prefix (no internal APIRouter(prefix=...)) under
`app.include_router(harness_router, prefix="/api")` in main.py, same
pattern as the existing `/api/stripe/webhook` compat shim
(routers/stripe_webhook_compat.py) — so the final path is exactly
`/api/harness/verify`, matching the real, already-shipped harness
client's hardcoded URL (confirmed directly from
github.com/AuremHQ/aurem-loop, main branch, loop.py:160:
`VERIFY_URL_DEFAULT = "https://auremcto.com/api/harness/verify"`).
There is deliberately no `/aurem-dev` segment here — that would break
an external client we cannot change.

This endpoint is called by an unauthenticated external client (the
downloaded harness binary) — it has no Authorization header and no
JWT. Its own credential is the {bin, token} pair in the body, verified
by services.harness_auth. It must never require current_dev/JWT auth.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from cto_services.db import require_db
from services.feature_flags import is_enabled
from services.harness_auth import verify_harness_identity
from services.harness_content_repo import fetch_harness_page_content
from services.harness_releases import get_latest_release
from services.rate_limiter import check_rate_limit_async, client_ip_from_request
from services.wsp_kit_stats import fetch_wsp_kit_live_stats

logger = logging.getLogger("aurem-dev.harness")

router = APIRouter()

_VERIFY_RATE_LIMIT_PER_MINUTE = 10
_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Same ceiling routers/support.py's public ticket form uses — generous
# for a real human, blocks scripted abuse of a zero-identity-proof form.
_WAITLIST_RATE_PER_IP_MIN = 5


class HarnessPageStatusResponse(BaseModel):
    harness_page_enabled: bool


# 2026-11-09 — public (no-auth) flag read, so the homepage's "Aurem
# Loop" promo section and the /harness page itself can both work for
# anonymous/signed-out visitors. Previously the ONLY place
# `harness_page_enabled` was exposed was the authenticated /auth/me
# response (routers/auth.py) — a signed-out visitor never calls that
# successfully, so pageFlags.js's `isHarnessPageEnabled()` silently
# stayed at its hardcoded default (false) for them regardless of the
# real admin-set flag value ("the pre-login flag gap"). This reads
# the SAME services.feature_flags "harness_page" doc `/auth/me` does
# — single source of truth, no second flag system to drift out of
# sync. `is_enabled()` with no user_id is already safe/correct for an
# anonymous caller (see services/feature_flags.py's own anonymous-
# canary-bucket handling) and has its own 60s process-local cache, so
# this adds no extra Mongo load under traffic.
@router.get("/harness/page-status", response_model=HarnessPageStatusResponse)
async def harness_page_status() -> HarnessPageStatusResponse:
    enabled = await is_enabled("harness_page")
    return HarnessPageStatusResponse(harness_page_enabled=enabled)


# 2026-11-10 — Phase A page content. Public (no-auth), same visibility
# pattern as page-status above: this does NOT itself check the
# harness_page flag (the caller — HarnessPage.jsx / seo-prerender.mjs
# — already gates the fetch on page-status being true first), so this
# stays a dumb, cheap content+stats read with no flag-logic duplicated
# here. `content` is fetched from the private AuremHQ/aurem-loop repo
# (services/harness_content_repo.py); `live_stats` overlays real-time
# stars/forks for the wsp-kit repo (services/wsp_kit_stats.py) — the
# two are intentionally separate fetches/caches so a GitHub outage on
# one never blocks the other.
@router.get("/harness/content")
async def harness_content() -> dict:
    content, live_stats = await asyncio.gather(
        fetch_harness_page_content(), fetch_wsp_kit_live_stats(),
    )
    return {"content": content, "live_stats": live_stats}


# 2026-11-10 — Phase B readiness check. Public (no-auth): version,
# artifact_sha256, changelog, mandatory, size_bytes and published_at
# are not secrets, and exposing them lets the website show "Download
# available" state WITHOUT a login. This is the one and only switch
# between Phase A ("not available yet" banner + waitlist) and Phase B
# (real download section) on the frontend — there is no separate
# feature flag for it. D1 (hosting a real package) is not approved as
# of this writing (see routers/admin_harness.py's own docstring), so
# get_latest_release() correctly returns None and `available` stays
# false until an admin explicitly publishes a real release. Never
# returns the artifact bytes themselves — only POST /aurem-dev/
# harness/download (routers/harness_downloads.py, login required)
# does that.
_RELEASE_PLATFORM = "windows"


@router.get("/harness/release-status")
async def harness_release_status() -> dict:
    db = require_db()
    release = await get_latest_release(db, platform=_RELEASE_PLATFORM)
    if not release:
        return {"available": False}
    return {
        "available": True,
        "version": release["version"],
        "platform": release["platform"],
        "sha256": release["artifact_sha256"],
        "changelog": release.get("changelog") or [],
        "mandatory": release.get("mandatory", False),
        "size_bytes": release.get("size_bytes"),
        "published_at": release.get("published_at"),
    }


class HarnessWaitlistRequest(BaseModel):
    email: str = Field(..., max_length=254)


# 2026-11-11 — real on-page waitlist (Fix 2). Previously "Join the
# waitlist" was a mailto-only link — did nothing in a browser with no
# mail client configured, which is most browsers in a sandboxed/clean
# test environment. Public, no-auth, per-IP rate-limited exactly like
# routers/support.py's `/tickets/public` (same zero-identity-proof
# shape). Upserts on email so a visitor double-clicking submit, or
# re-joining later, never creates duplicate rows — `joined_at` is
# left untouched on a repeat submit (first-join date is what matters
# for the eventual "email the waitlist when download goes live" step).
@router.post("/harness/waitlist")
async def harness_waitlist_join(body: HarnessWaitlistRequest, request: Request) -> dict:
    email = (body.email or "").strip().lower()
    if not email or not _EMAIL_RX.match(email):
        raise HTTPException(400, "Please enter a valid email address.")

    client_ip = client_ip_from_request(request)
    if not await check_rate_limit_async(
        f"harness-waitlist-ip:{client_ip}", _WAITLIST_RATE_PER_IP_MIN,
    ):
        raise HTTPException(429, "Too many requests from this network. Try again in a minute.")

    db = require_db()
    await db.harness_waitlist.update_one(
        {"email": email},
        {
            "$setOnInsert": {"email": email, "joined_at": time.time(), "source": "harness_page"},
            "$set": {"last_submitted_at": time.time()},
        },
        upsert=True,
    )
    return {"ok": True}


class HarnessVerifyRequest(BaseModel):
    bin: str = Field(..., min_length=1, max_length=256)
    token: str = Field(..., min_length=1, max_length=512)
    harness_version: str = Field("", max_length=64)


class HarnessVerifyResponse(BaseModel):
    subscriber: bool
    plan: str | None = None


@router.post("/harness/verify", response_model=HarnessVerifyResponse)
async def harness_verify(body: HarnessVerifyRequest) -> HarnessVerifyResponse:
    """One hidden POST from the harness on every start: {bin, token,
    harness_version} -> {subscriber, plan}. harness_version is
    informational only — never used to refuse an older client here.

    Rate-limited per bin, not globally, so one noisy installation
    cannot 429 every other user."""
    allowed = await check_rate_limit_async(
        f"harness_verify:{body.bin}", _VERIFY_RATE_LIMIT_PER_MINUTE,
    )
    if not allowed:
        raise HTTPException(429, "Too many verify attempts, try again shortly")

    db = require_db()
    result = await verify_harness_identity(db, bin=body.bin, token=body.token)
    return HarnessVerifyResponse(**result)
