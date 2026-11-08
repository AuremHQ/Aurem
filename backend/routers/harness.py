"""
routers/harness.py — Aurem Loop harness, Phase A verify endpoint only.

Scope note (explicit, per the approved plan's own restriction): this
file builds ONLY `POST /api/harness/verify`. No billing, no telemetry
ingestion, no cloud-run endpoint — those are separate, later phases
and are deliberately not touched here.

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

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from cto_services.db import require_db
from services.harness_auth import verify_harness_identity
from services.rate_limiter import check_rate_limit_async

logger = logging.getLogger("aurem-dev.harness")

router = APIRouter()

_VERIFY_RATE_LIMIT_PER_MINUTE = 10


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
