"""
services/token_revocation.py — Iter 307

JWT revocation store + per-user session barrier.

Two orthogonal revocation primitives:

  1. `revoked_tokens` collection — one document per revoked jti, with a
     TTL index on `expires_at`. MongoDB's TTL monitor auto-deletes docs
     once past that timestamp (checked ~every 60 s), so the collection
     bounded by the JWT TTL (7 days) — it does NOT grow unbounded.
     Used by `/auth/logout` to kill exactly the one token that logged out.

  2. `dev_users.session_barrier_at` — a per-user unix timestamp. Any
     JWT with `iat < session_barrier_at` for that user is rejected.
     Used by "revoke all sessions" — O(1) write instead of enumerating
     every active jti. When the founder suspects a specific token
     leaked they can nuke every active session for one user without
     touching global state.

Design notes:
  - `is_jti_revoked` and the per-user barrier check are on the HOT
    PATH of every authenticated request. Both are keyed on indexed
    fields; per-request cost measured on preview: sub-3ms combined.
  - 2026-11-25 (Part VII audit fix 1(b)) — REVERSAL of the Iter 307
    trade-off, deliberate and availability-affecting: revocation
    checks are now FAIL-CLOSED. The old docstring argued fail-open
    ("if the DB is momentarily down the session continues rather
    than logging everyone out", citing Amazon/GitHub). That meant a
    revoked JWT stayed usable for the whole duration of any Mongo
    outage. As of this change, if the DB cannot answer a revocation
    check AND no fresh cached answer exists, the check raises
    `RevocationCheckUnavailable` and callers must deny the request
    (401) — a Mongo blip now 401s authed traffic instead of trusting
    revoked tokens. The write side was already fail-closed (logout
    500s so the caller retries) and stays that way.
  - To keep a *transient* blip from locking everyone out, both
    checks keep a short-TTL (30 s) in-process cache of their last
    GOOD answer (positive and negative). On a DB error a fresh
    cached answer is served; only a stale/missing cache entry
    denies. Worst case a token revoked within the last 30 s is
    still accepted — that bounded window is the price of surviving
    a blip, and it is documented here, not hidden.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

REVOKED_COLL = "revoked_tokens"


class RevocationCheckUnavailable(Exception):
    """Raised by the revocation checks when the DB cannot answer AND
    no fresh cached answer exists. Callers MUST treat this as "deny"
    (fail closed) — never as "not revoked". See module docstring."""


# ── Short-TTL cache for the check side (Part VII fix 1(b)) ──────────
# In-process, per-worker cache of the last GOOD revocation answer, so
# a transient Mongo blip does not instantly 401 every authed request.
# Positive answers are sticky by construction (a revoked jti never
# un-revokes; a barrier only moves forward), so the TTL bounds only
# the *negative* staleness: a token revoked within the last
# REVOCATION_CACHE_TTL_S seconds may still be accepted. 30 s is short
# enough that the window is negligible next to the 7-day JWT TTL, and
# long enough to ride out a sub-minute DB hiccup.
REVOCATION_CACHE_TTL_S = 30.0
_REVOCATION_CACHE_MAX_ENTRIES = 10_000

# key: ("jti", jti) | ("barrier", user_id, iat) -> (answer, expires_mono)
_revocation_cache: dict[tuple, tuple[bool, float]] = {}


def _cache_get(key: tuple) -> Optional[bool]:
    entry = _revocation_cache.get(key)
    if entry is None:
        return None
    answer, expires_mono = entry
    if time.monotonic() >= expires_mono:
        del _revocation_cache[key]
        return None
    return answer


def _cache_set(key: tuple, answer: bool) -> None:
    if len(_revocation_cache) >= _REVOCATION_CACHE_MAX_ENTRIES:
        now = time.monotonic()
        for k in [k for k, (_, exp) in _revocation_cache.items()
                  if exp <= now]:
            del _revocation_cache[k]
    _revocation_cache[key] = (answer, time.monotonic() + REVOCATION_CACHE_TTL_S)


def clear_revocation_cache() -> None:
    """Drop all cached revocation answers. Tests + process restarts
    only — never called on the request path."""
    _revocation_cache.clear()


# ────────────────────────── indexes ──────────────────────────

async def ensure_indexes(db) -> None:
    """Idempotent index setup. Called from the FastAPI lifespan hook."""
    if db is None:
        return
    coll = db[REVOKED_COLL]
    # TTL index — Mongo auto-deletes docs once `expires_at` is in the past.
    # `expireAfterSeconds=0` means "expire AT the timestamp stored in
    # `expires_at`". The docs disappear WITHIN ~60 s of expiry (Mongo TTL
    # monitor cadence).
    await coll.create_index("expires_at", expireAfterSeconds=0,
                            name="revoked_tokens_ttl")
    # Fast-lookup index for the check on every authed request.
    await coll.create_index("jti", unique=True, name="revoked_tokens_jti")
    # Per-user barrier index — makes the "list all my logouts" audit
    # query cheap. Not on the hot path (barrier check reads dev_users).
    await coll.create_index("user_id", name="revoked_tokens_user_id")


# ────────────────────────── writes ───────────────────────────

async def revoke_jti(db, jti: str, exp: int,
                     user_id: Optional[str] = None,
                     reason: str = "logout") -> bool:
    """Insert a jti into the revocation store. Idempotent — a second
    revocation attempt for the same jti is a no-op (matches upsert
    semantics). Returns True on write, False on DB unavailable."""
    if db is None or not jti:
        return False
    from datetime import datetime, timezone
    try:
        # `exp` is a unix timestamp from the JWT `exp` claim. Store as
        # a datetime so Mongo's TTL monitor can act on it (TTL indexes
        # only work on real BSON dates, not epoch ints).
        expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        await db[REVOKED_COLL].update_one(
            {"jti": jti},
            {"$setOnInsert": {
                "jti":        jti,
                "user_id":    user_id,
                "reason":     reason,
                "revoked_at": now,
                "expires_at": expires_at,
            }},
            upsert=True,
        )
        # Write-through: a revoked jti is revoked immediately on this
        # worker too — no waiting for the next DB round-trip / cache TTL.
        _cache_set(("jti", jti), True)
        return True
    except Exception as e:
        logger.warning("revoke_jti failed for jti=%s: %s", jti, e)
        return False


async def revoke_all_for_user(db, user_id: str,
                              reason: str = "founder_nuke") -> int:
    """Set the per-user session barrier to `now`. Every JWT for this
    user with an `iat` earlier than the barrier is rejected on the
    next request. Returns the modified_count from Mongo (1 if the row
    exists, 0 otherwise).

    NB: Users with no persisted dev_users row (edge case — should not
    happen) get 0. Callers should check the return value if they need
    a hard guarantee.
    """
    if db is None or not user_id:
        return 0
    now = int(time.time())
    res = await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {"session_barrier_at": now,
                  "session_barrier_reason": reason}},
    )
    # The barrier just moved forward, so every cached "not blocked"
    # answer for this user is now wrong — drop them so the next check
    # re-reads the new barrier instead of serving a stale negative
    # for up to REVOCATION_CACHE_TTL_S.
    for key in [k for k in _revocation_cache
                if k[0] == "barrier" and k[1] == user_id]:
        del _revocation_cache[key]
    return res.modified_count


# ────────────────────────── reads (hot path) ─────────────────

async def is_jti_revoked(db, jti: str) -> bool:
    """One indexed find_one, with a short-TTL cache of the last good
    answer. FAIL-CLOSED (Part VII fix 1(b)): on a DB error with no
    fresh cached answer, raises RevocationCheckUnavailable — callers
    must deny the request, never treat the token as not-revoked.
    Tokens without a jti (pre-iter-212m-55) still return False: there
    is nothing to look up, and the barrier check still applies."""
    if not jti:
        return False
    key = ("jti", jti)
    if db is None:
        cached = _cache_get(key)
        if cached is not None:
            return cached
        raise RevocationCheckUnavailable("database unavailable (no db handle)")
    try:
        doc = await db[REVOKED_COLL].find_one({"jti": jti}, {"_id": 1})
        answer = doc is not None
    except Exception as e:
        logger.warning("is_jti_revoked lookup failed: %s", e)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        raise RevocationCheckUnavailable(f"revocation lookup failed: {e}") from e
    _cache_set(key, answer)
    return answer


async def is_iat_before_barrier(db, user_id: str, iat: Optional[int]) -> bool:
    """Return True iff the token's `iat` predates the user's
    `session_barrier_at`. Tokens without an `iat` claim (pre-iter-212m-55)
    are never barrier-blocked — they're already handled by JWT_SECRET
    rotation if needed. FAIL-CLOSED like `is_jti_revoked` above:
    a DB error with no fresh cached answer raises
    RevocationCheckUnavailable instead of returning False."""
    if not user_id or iat is None:
        return False
    key = ("barrier", user_id, int(iat))
    if db is None:
        cached = _cache_get(key)
        if cached is not None:
            return cached
        raise RevocationCheckUnavailable("database unavailable (no db handle)")
    try:
        row = await db.dev_users.find_one(
            {"user_id": user_id},
            {"session_barrier_at": 1, "_id": 0},
        )
    except Exception as e:
        logger.warning("is_iat_before_barrier lookup failed: %s", e)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        raise RevocationCheckUnavailable(f"barrier lookup failed: {e}") from e
    if not row:
        _cache_set(key, False)
        return False
    barrier = row.get("session_barrier_at")
    if not barrier:
        _cache_set(key, False)
        return False
    answer = int(iat) < int(barrier)
    _cache_set(key, answer)
    return answer
