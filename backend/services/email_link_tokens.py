"""
services/email_link_tokens.py — shared HMAC token/link utilities for
outbound transactional + campaign email (2026-09-26).

Extracted from services/first50_campaign.py when the First-50 signup
promo was fully removed from the codebase (0 users had ever claimed
it, so the removal is a clean cut — see routers/auth_verify.py for
the promo-free /auth/verify + /auth/resend-verification endpoints).

These four helpers are genuinely generic (unsubscribe links + a
no-login "message us" support link) and are reused by several
UNRELATED email senders that have nothing to do with the removed
promo: routers/support.py, routers/email_unsubscribe.py,
services/support_email.py, services/winback_campaign.py,
services/gate_stuck_nudge.py, services/funnel_nudge_cron.py.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import quote

PUBLIC_BASE  = os.environ.get("PUBLIC_APP_URL", "https://auremcto.com").rstrip("/")
UNSUB_SECRET = os.environ.get("UNSUBSCRIBE_SECRET", "aurem-unsub-secret-rotate-me")


def unsub_token(email: str) -> str:
    return hmac.new(UNSUB_SECRET.encode(), email.lower().strip().encode(),
                    hashlib.sha256).hexdigest()[:16]


def unsub_url(email: str) -> str:
    return (f"{PUBLIC_BASE}/api/aurem-dev/unsubscribe"
            f"?t={unsub_token(email)}&e={quote(email)}")


# ── Support-token (same HMAC pattern, different scope) ────────────────
# Used by the "Need help?" links in campaign/transactional emails.
# Verified by the public POST /support/tickets/token endpoint so users
# don't have to log in to send a message.
def support_token(email: str) -> str:
    payload = f"support:{email.lower().strip()}"
    return hmac.new(UNSUB_SECRET.encode(), payload.encode(),
                    hashlib.sha256).hexdigest()[:16]


def support_url(email: str, source: str) -> str:
    return (f"{PUBLIC_BASE}/support"
            f"?t={support_token(email)}&e={quote(email)}&src={quote(source)}")


async def is_unsubscribed(db, email: str) -> bool:
    doc = await db.email_unsubscribes.find_one(
        {"email": (email or "").lower().strip()}, {"_id": 1},
    )
    return doc is not None
