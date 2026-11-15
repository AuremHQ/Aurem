"""
routers/dot.py — ORA dot signup list, 2026-11-14.

Public (no-auth) signup form on the /dot page: a visitor leaves a name
+ email and joins the ORA dot list (Mongo collection `dot_signups`).
Same zero-identity-proof shape as routers/harness.py's POST
/harness/waitlist (Fix 2, 2026-11-11): per-IP rate-limited, upsert on
email so a double-click or a later re-join never creates duplicate
rows — `joined_at` keeps the FIRST join date, `name` is refreshed on
re-submit (people change names, not emails).

ORA dot is a separate product from ORA and from Aurem Loop: its list
lives in its own collection (`dot_signups`, NOT `harness_waitlist`,
NOT the ORA users collection), and a /dot signup never creates an ORA
account — account signup/signin is the EXISTING auth flow, reached
from /dot via /signup?next=/dot and /login?next=/dot (Fix 7 gating,
which already carries `next` through email + GitHub + Google OAuth).

No download exists yet (product Phase 0-1 not built) — this list is
only "tell me when it's ready". No payment, no secrets, no new auth.
"""
from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from cto_services.db import require_db
from services.rate_limiter import check_rate_limit_async, client_ip_from_request

logger = logging.getLogger("aurem.dot")

router = APIRouter()

_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Same ceiling routers/support.py's public ticket form and the
# /harness waitlist use — generous for a real human, blocks scripted
# abuse of a zero-identity-proof form.
_SIGNUP_RATE_PER_IP_MIN = 5
_MAX_NAME_LEN = 100


def _clean_name(raw: str) -> str:
    # One line, no control characters — this goes straight into the
    # /admin table, so it must not carry newlines or terminal escapes.
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", raw or "")
    return " ".join(cleaned.split())[:_MAX_NAME_LEN]


class DotSignupRequest(BaseModel):
    name: str = Field(..., max_length=_MAX_NAME_LEN)
    email: str = Field(..., max_length=254)


@router.post("/dot/signup")
async def dot_signup(body: DotSignupRequest, request: Request) -> dict:
    name = _clean_name(body.name)
    if not name:
        raise HTTPException(400, "Please enter your name.")
    email = (body.email or "").strip().lower()
    if not email or not _EMAIL_RX.match(email):
        raise HTTPException(400, "Please enter a valid email address.")

    client_ip = client_ip_from_request(request)
    if not await check_rate_limit_async(
        f"dot-signup-ip:{client_ip}", _SIGNUP_RATE_PER_IP_MIN,
    ):
        raise HTTPException(429, "Too many requests from this network. Try again in a minute.")

    db = require_db()
    await db.dot_signups.update_one(
        {"email": email},
        {
            "$setOnInsert": {"email": email, "joined_at": time.time(), "source": "dot_page"},
            "$set": {"name": name, "last_submitted_at": time.time()},
        },
        upsert=True,
    )
    return {"ok": True}
