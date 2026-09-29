"""
routers/auth_verify.py — email verification (2026-09-26).

Renamed from routers/promo_first50.py when the First-50 signup promo
was fully removed from the codebase (0 users had ever claimed it —
confirmed live in Mongo before deleting anything, so this was a clean
cut, not a data migration). This file now does ONLY what its name
says: verify a signup email and let a user request a fresh link.

Endpoints:
  • GET  /api/aurem-dev/auth/verify?token=xyz
        Public single-click verification. Steps (all atomic):
          1. Consume the `email_verifications` row (findAndModify
             where `used_at IS NULL AND expires_at > now`).
          2. Mark `dev_users.email_verified = true`.
          3. 302 redirect to `/verify?status=ok` on the frontend so
             the founder sees a themed success page.

  • POST /api/aurem-dev/auth/resend-verification
        Authenticated. Lets a logged-in unverified user request a
        fresh link if the first email was lost. Rate-limited via
        the existing global 300/min/IP middleware; per-user cap of
        1 resend / 15 min enforced in this handler.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.verification_email import send_verification_email

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth Verify"])

RESEND_COOLDOWN_MIN = 15
PUBLIC_BASE = os.environ.get("PUBLIC_APP_URL", "https://auremcto.com").rstrip("/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── The verification click ───────────────────────────────────────────
def _redirect_to_frontend(status: str, reason: str = "") -> RedirectResponse:
    """302 to `/verify?...` on the frontend. Never leaks the token."""
    params = {"status": status}
    if reason:
        params["reason"] = reason
    target = f"{PUBLIC_BASE}/verify?{urlencode(params)}"
    return RedirectResponse(url=target, status_code=302)


@router.get("/auth/verify")
async def verify_email(token: str = ""):
    """Single-use email verification. 302-redirects to the frontend
    success/error page. NEVER echoes the token back to the client."""
    db = get_db()
    if db is None:
        return _redirect_to_frontend("error", reason="db_unavailable")
    if not token or not token.strip():
        return _redirect_to_frontend("error", reason="missing_token")

    now = _now()
    # ── Step 1: atomically consume the token ─────────────────────
    # findOneAndUpdate ensures a single-use guarantee — two concurrent
    # clicks can't both mark the same token as used.
    row = await db.email_verifications.find_one_and_update(
        {"token": token, "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}},
        return_document=True,
    )
    if not row:
        # Distinguish expired vs already-used vs unknown for the UI.
        existing = await db.email_verifications.find_one(
            {"token": token},
            {"_id": 0, "used_at": 1, "expires_at": 1},
        )
        if not existing:
            return _redirect_to_frontend("error", reason="invalid_token")
        if existing.get("used_at"):
            return _redirect_to_frontend("ok", reason="already_verified")
        return _redirect_to_frontend("error", reason="expired_token")

    user_id = row["user_id"]

    # ── Step 2: mark user verified (idempotent) ──────────────────
    user_doc = await db.dev_users.find_one_and_update(
        {"user_id": user_id},
        {"$set": {
            "email_verified":    True,
            "email_verified_at": now,
        }},
        return_document=True,
    )
    if not user_doc:
        logger.warning("verify: user %s not found for token", user_id)
        return _redirect_to_frontend("error", reason="user_not_found")

    # 2026-08-20 — real funnel_events row so `/admin/funnel`'s
    # `event_counts` (aggregate) and the per-user Activity Log both
    # see this milestone. Previously only inferable from
    # `email_verifications.used_at`, which the aggregate endpoint
    # never queries.
    from services.signup_guards import emit_funnel_event
    await emit_funnel_event(
        db, user_id=user_id, event_type="email_verified", metadata={},
    )

    # Track 3 (item #33) — fire the post-verification welcome email
    # as a safe_bg task. Idempotent by campaign + user_id inside the
    # welcome module itself, so a second verify click is a no-op.
    try:
        from services.welcome_email import send_welcome_email
        from services.bg_safe import safe_bg
        import asyncio as _asyncio
        _asyncio.create_task(safe_bg(send_welcome_email)(db, {
            "user_id": user_doc["user_id"],
            "email":   user_doc.get("email", ""),
            "name":    user_doc.get("name", ""),
        }))
    except Exception as e:                              # noqa: BLE001
        logger.warning("welcome email dispatch failed: %r", e)

    return _redirect_to_frontend("ok")


# ── Resend verification link ─────────────────────────────────────────
@router.post("/auth/resend-verification")
async def resend_verification(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Authenticated user asks for a fresh verification link.
    Cooldown: 1 resend per 15 min per user. Silent no-op for already
    verified users (returns ok=True so we don't leak account state)."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    user = await db.dev_users.find_one(
        {"user_id": me["user_id"]},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1,
         "email_verified": 1},
    )
    if not user:
        raise HTTPException(404, "user not found")
    if user.get("email_verified"):
        return {"ok": True, "already_verified": True}

    # Cooldown check via last `sent_at` on our audit row.
    cutoff = _now() - timedelta(minutes=RESEND_COOLDOWN_MIN)
    recent = await db.onboarding_emails.find_one(
        {"user_id": user["user_id"], "campaign": "email_verification",
         "sent_at": {"$gt": cutoff}},
        {"_id": 1},
    )
    if recent:
        raise HTTPException(
            429,
            f"Please wait {RESEND_COOLDOWN_MIN} minutes between "
            "verification-email requests.",
        )
    result = await send_verification_email(db, user)
    return {"ok": bool(result.get("ok"))}
