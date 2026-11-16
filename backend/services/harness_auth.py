"""
services/harness_auth.py — Aurem Loop harness, Phase A verify.

Issues and verifies the long-lived credential the downloadable Aurem
Loop harness sends on every startup to `POST /api/harness/verify`
(contract confirmed directly from the real shipped client:
github.com/AuremHQ/aurem-loop, main branch, loop.py:160-229).

`bin` is the existing `user_id` — this codebase's own admin-panel
nickname for it (routers/admin_bin.py's BIN Tracker already does
`db.dev_users.find_one({"user_id": bin_id})`), not a new identifier
system. `token` is new: a long random string, stored ONLY as a SHA-256
digest (never the plaintext, never returned once issued — same
"stored like a password" rule the plan specifies), scoped to exactly
one `user_id`. A token must match the bin it is presented with; a bin
alone (even a real one) is never sufficient — see
verify_harness_identity's dual-match query.

Fails closed everywhere: any ambiguity, missing field, unknown tier,
or DB error resolves to {"subscriber": False, "plan": None}, never an
exception escaping to the caller.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time

from services.subscription_tiers import Tier, _coerce

logger = logging.getLogger("aurem-dev.harness_auth")

_TOKEN_PREFIX = "hlt_"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_harness_token(db, *, user_id: str) -> str:
    """Mint a new harness verify-token for user_id. Returns the
    PLAINTEXT token exactly once, to be written into the download's
    identity.json — it is never stored or retrievable again.

    Re-downloading (calling this again for the same user_id) issues a
    second, independent token; it never revokes the first."""
    token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    await db.harness_tokens.insert_one({
        "token_hash": _hash_token(token),
        "user_id": user_id,
        "created_at": time.time(),
        "revoked_at": None,
    })
    return token


async def verify_harness_identity(db, *, bin: str, token: str) -> dict:
    """Returns {"subscriber": bool, "plan": str | None}.

    bin and token must BOTH be given and must belong to each other —
    a bin alone, even a real one, never authenticates. Fails closed on
    any error or ambiguity rather than raising."""
    if not bin or not isinstance(bin, str) or not token or not isinstance(token, str):
        return {"subscriber": False, "plan": None}
    try:
        row = await db.harness_tokens.find_one({
            "token_hash": _hash_token(token),
            "user_id": bin,
            "revoked_at": None,
        })
    except Exception as e:                                       # noqa: BLE001
        logger.warning("verify_harness_identity lookup failed (fail-closed): %r", e)
        return {"subscriber": False, "plan": None}
    if not row:
        return {"subscriber": False, "plan": None}

    try:
        user = await db.dev_users.find_one(
            {"user_id": bin}, {"tier": 1, "_id": 0},
        )
    except Exception as e:                                       # noqa: BLE001
        logger.warning("verify_harness_identity user lookup failed (fail-closed): %r", e)
        return {"subscriber": False, "plan": None}
    if not user:
        return {"subscriber": False, "plan": None}

    tier_str = user.get("tier") or "free"
    tier_enum = _coerce(tier_str)
    if tier_enum == Tier.FREE:
        return {"subscriber": False, "plan": "free"}
    return {"subscriber": True, "plan": tier_str}


async def check_harness_token(db, *, bin: str, token: str) -> bool:
    """Plain validity check for a (bin, token) pair — any valid
    identity (free or paid), unlike verify_harness_identity's
    subscriber-only `subscriber` flag. Built on verify_harness_identity
    rather than re-querying harness_tokens directly: `plan` already
    disambiguates "valid free tier" (plan="free") from "invalid/
    unknown/revoked" (plan=None) in its return shape, so no new query
    logic is needed. Used by the telemetry endpoint, where any
    authenticated harness — not just subscribers — may send."""
    identity = await verify_harness_identity(db, bin=bin, token=token)
    return identity["plan"] is not None


async def revoke_harness_token(db, *, user_id: str, token: str | None = None) -> int:
    """Revoke one specific token (if given) or ALL active tokens for
    user_id. Idempotent — revoking an already-revoked or nonexistent
    token is a no-op, not an error. Returns the number of rows newly
    revoked (0 if none matched).

    Per the plan: revocation locks account-service/paid-feature access
    only — it must never be used to delete local user data, and the
    harness client itself (confirmed from the real shipped code) keeps
    running free/local tasks when verify reports not-subscriber."""
    filt: dict = {"user_id": user_id, "revoked_at": None}
    if token:
        filt["token_hash"] = _hash_token(token)
    try:
        result = await db.harness_tokens.update_many(
            filt, {"$set": {"revoked_at": time.time()}},
        )
    except Exception as e:                                       # noqa: BLE001
        logger.warning("revoke_harness_token failed (non-fatal): %r", e)
        return 0
    return getattr(result, "modified_count", 0) or 0
