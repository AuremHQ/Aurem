"""
routers/auth_verify.py — email verification (2026-09-26).

Renamed from routers/promo_first50.py when the First-50 signup promo
was fully removed from the codebase (0 users had ever claimed it —
confirmed live in Mongo before deleting anything, so this was a clean
cut, not a data migration). This file now does ONLY what its name
says: verify a signup email and let a user request a fresh link.

Endpoints:
  • POST /api/aurem-dev/auth/verify
        JSON body { "token": "<value>" }.
        Public single-click verification. Steps (all atomic):
          1. Consume the `email_verifications` row (findAndModify
             where `used_at IS NULL AND expires_at > now`).
          2. Mark `dev_users.email_verified = true`.
          3. 302 redirect to `/verify?status=ok` on the frontend so
             the founder sees a themed success page.
        Token is accepted via JSON body instead of a URL query param
        so it never appears in proxy logs, browser history,
        or the Referer header.

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

from fastapi import APIRouter, Body, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.verification_email import send_verification_email


logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth Verify"])

RESEND_COOLDOWN_MIN = 15
PUBLIC_BASE = os.environ.get("PUBLIC_APP_URL", "https://auremcto.com").rstrip("/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _VerifyBody(BaseModel):
    """Payload for /auth/verify — keeps the secret off the URL line."""
    token: str = ""


# ── The verification click ───────────────────────────────────────────
def _redirect_to_frontend(status: str, reason: str = "") -> RedirectResponse:
    """302 to `/verify?...` on the frontend. Never leaks the token."""
    params = {"status": status}
    if reason:
        params["reason"] = reason
    target = f"{PUBLIC_BASE}/verify?{urlencode(params)}"
    return RedirectResponse(url=target, status_code=302)


@router.post("/auth/verify")
async def verify_email(
    payload: _VerifyBody,
    authorization: Optional[str] = Header(None),
):
    """Single-use email verification via JSON body. 302-redirects to the
    frontend success/error page. NEVER echoes the token back to the client."""
    token = payload.token

    db = get_db()
    if db is None:
        return _redirect_to_frontend("error", reason="db_unavailable")
    if not token or not token.strip():
        return _redirect_to_frontend("error", reason="missing_token")

    now = _now()
    # ── Step 1: atomically consume the token ─────────────────────
    # findOneAndUpdate ensures a single-use