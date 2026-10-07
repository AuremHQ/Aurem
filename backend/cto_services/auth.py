"""
cto_services/auth.py — AUREM Dev
JWT authentication for developer routes.
"""
import contextvars
import logging
import os
import time
import jwt
from fastapi import HTTPException, Header, Request
from typing import Optional

logger = logging.getLogger("aurem.auth")

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"

# Admin Team S2 (AT1b) / D19 — per-request cache of the LAST successful
# admin decision, so a redundant INLINE check (e.g. admin_vanguard.py's,
# which runs after the router-level `Depends(require_admin_dep)` already
# approved the SAME request) can reuse that decision instead of
# re-deriving it with less context (no `request` => no registry lookup
# => would wrongly reject an approved team member). Each request runs in
# its own asyncio Task with its own COPY of the context, so a `.set()`
# here can never leak into a different request — only deeper `await`s
# within the SAME request/Task see it.
_admin_decision: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "aurem_admin_decision", default=None,
)


def reuse_admin_decision(user_id: str) -> Optional[dict]:
    """D19 — true only if THIS request already passed the router-level
    gate for this exact user. Returns the approved user dict, or None."""
    cached = _admin_decision.get()
    if cached is not None and cached.get("user_id") == user_id:
        return cached
    return None


# Admin Team S2 (AT1b) — `require_admin_dep` (the router-level dependency
# used by ~24 admin routers) sets this BEFORE calling `require_admin`, so
# `require_admin`'s own signature/call can stay exactly
# `require_admin(authorization)` wherever a `Request` isn't explicitly on
# hand (same isolation guarantee as `_admin_decision` above — one copy of
# the context per request/Task, no cross-request leakage).
_current_request: "contextvars.ContextVar[Optional[Request]]" = contextvars.ContextVar(
    "aurem_current_request", default=None,
)


# 2026-09-28 MASTER TASK Part B — Live Presence.
# In-process, per-worker cache of the last time we WROTE last_seen_at
# for a given user_id, keyed by monotonic time. `current_dev` runs on
# every single authenticated request across the whole app, so without
# this throttle every request would fire a Mongo update. 45s means the
# green-dot/"active within 2 minutes" bar in the admin panel is always
# well within its own freshness window even under this throttle.
_PRESENCE_WRITE_THROTTLE_S = 45.0
_last_presence_write: dict[str, float] = {}


def _touch_presence(user_id: str) -> None:
    """Fire-and-forget `dev_users.last_seen_at` bump — never awaited
    inline, never allowed to add latency or fail the real request."""
    now_mono = time.monotonic()
    last = _last_presence_write.get(user_id)
    if last is not None and (now_mono - last) < _PRESENCE_WRITE_THROTTLE_S:
        return
    _last_presence_write[user_id] = now_mono

    async def _write():
        try:
            from cto_services.db import get_db
            db = get_db()
            if db is None:
                return
            await db.dev_users.update_one(
                {"user_id": user_id}, {"$set": {"last_seen_at": time.time()}},
            )
        except Exception as e:  # noqa: BLE001 — presence is best-effort
            logger.debug("presence touch failed for %s: %r", user_id, e)

    try:
        import asyncio
        asyncio.ensure_future(_write())
    except RuntimeError:
        pass


async def current_dev(authorization: Optional[str] = None) -> dict:
    """Verify Bearer JWT and return payload enriched with the latest user
    row from MongoDB (tier, is_unlimited, plan, etc.). Raises 401 if
    invalid. Iter 50.1 — DB enrichment so rate-limit / cap checks can
    correctly bypass founders without each caller re-fetching the user."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization format")
    token = parts[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        # 2026-08-24 · Pillar 5 fix — this used to interpolate the raw
        # PyJWT exception string into `detail` (e.g. a decode error can
        # include a raw-bytes codec message), leaking internal detail
        # to the client. Log the real exception server-side only.
        logger.info("current_dev: invalid token rejected: %r", e)
        raise HTTPException(status_code=401, detail="Invalid token")
    # Iter 307 — JWT revocation gate. Two orthogonal checks, both cheap
    # (one indexed find_one each, sub-3ms combined on preview):
    #   1. jti in `revoked_tokens` collection → user hit /auth/logout
    #      or an admin nuked this specific token.
    #   2. iat < dev_users.session_barrier_at → an admin (or the user
    #      themselves via /auth/revoke-all-sessions) declared every
    #      token issued before `barrier_at` invalid.
    # See services/token_revocation.py module docstring for the
    # fail-open-on-DB-hiccup rationale.
    try:
        from cto_services.db import get_db
        from services import token_revocation as _rev
        db_for_check = get_db()
        jti = payload.get("jti")
        if jti and await _rev.is_jti_revoked(db_for_check, jti):
            raise HTTPException(status_code=401, detail="Token has been revoked")
        uid = payload.get("user_id")
        iat = payload.get("iat")
        if uid and iat and await _rev.is_iat_before_barrier(db_for_check, uid, iat):
            raise HTTPException(status_code=401, detail="All sessions revoked — sign in again")
    except HTTPException:
        raise
    except Exception:
        # DB unavailable → fail-open on the CHECK side. See module
        # docstring in services/token_revocation.py — matches industry
        # trade-off (Amazon, GitHub) and the codebase's existing
        # `require_admin` pattern of swallowing DB hiccups.
        pass
    # Enrich with DB flags so callers see fresh is_unlimited / tier values.
    # Admin Team S2 (AT1b) / D12 — also stamp `_row_lookup` so the admin
    # gate below can tell "found a live dev_users row" (required for BOTH
    # the Owner and team-member paths) apart from "no row" (403) and "DB
    # raised" (503), without changing what this function returns to any
    # of its many NON-admin callers (rate limits, chat, etc.) — none of
    # them read this key, so their behaviour is unchanged.
    payload["_row_lookup"] = "not_attempted"
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is not None and payload.get("user_id"):
            u = await db.dev_users.find_one(
                {"user_id": payload["user_id"]},
                {"_id": 0, "tier": 1, "is_unlimited": 1, "is_admin": 1,
                 "is_founder": 1, "plan": 1, "plan_limit": 1, "email": 1},
            )
            if u:
                payload = {**payload, **u, "_row_lookup": "found"}
            else:
                payload["_row_lookup"] = "not_found"
    except Exception:
        payload["_row_lookup"] = "db_error"
    if payload.get("user_id"):
        _touch_presence(payload["user_id"])
    return payload


def is_admin_or_founder(user: dict) -> bool:
    """Shared privilege predicate for tier-bypass decisions (uploads,
    image-gen, quotas). Same rule as ``require_admin`` minus the raise."""
    tier = (user.get("tier") or "").strip().lower()
    return bool(user.get("is_admin") or user.get("is_founder") or tier == "founder")


def _legacy_admin_bypass(user: dict) -> bool:
    """Admin Team S2 (AT1b) / D15 — ALWAYS False in shipped code; this is
    not a flag, env var, or config switch, it is a hardcoded `return False`
    that no request can influence. `tests/conftest.py`'s one autouse
    fixture monkeypatches this exact function (not any production code
    path) to restore the pre-S2 `is_admin`/`tier=="founder"` acceptance
    for the ~265 pre-existing tests across this suite that construct a
    legacy "admin" user and call one of `require_admin`'s ~270 delegates
    directly, so none of them need editing. The new Admin Team lock tests
    opt out of that fixture (`@pytest.mark.real_admin_gate`) and exercise
    this real function, which never bypasses anything.
    """
    return False


def _matched_route(request) -> Optional[tuple]:
    if request is None:
        request = _current_request.get()
    if request is None:
        return None
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    return (request.method, path)


async def require_admin(authorization: Optional[str] = None, request=None) -> dict:
    """Admin Team S2 (AT1b) — THE one admin gate (D19). Every other
    "admin check" definition in the codebase (``routers/_admin_common.py``,
    ``admin.py``, ``mfa.py``, ``admin_vanguard.py``, ``admin_bin.py``) now
    delegates to this function or to `_finish_admin_check` below, which
    holds the actual decision so test-mock-compatible wrappers (that must
    keep their own local ``current_dev`` call, see `_admin_common.py`)
    still run the exact same rule.

    The rule (D1/D6/D7/D9/D12/D13/D14/D18/D19/D21):
      1. Decode + enrich the JWT via ``current_dev`` (sets ``_row_lookup``).
      2. Legacy-test bypass hook — always a no-op in production (D15).
      3. D12 — no confirmed CURRENT ``dev_users`` row => fail closed:
         DB exception => 503, no row at all => 403. Never trusts the bare
         JWT ``is_admin`` claim for this decision.
      4. Owner — the live row's email is on the ``FOUNDER_EMAILS``
         allow-list (D1/D14/D18). `is_admin`/`tier=="founder"`/`is_founder`
         ALONE grant nothing any more. Owner needs no MFA claim (D6 — PIN
         tokens still work for Owner) and bypasses the route registry
         entirely (full access to every screen, including owner-only
         ones).
      5. Team member — an ACCEPTED, ``status=="active"`` row in
         ``admin_team_members`` keyed by ``user_id`` (D7/D21 — this path
         never looks at or implies ``dev_users.is_admin``). Their access is
         capped by the route registry's required (screen_key, level) for
         THIS request (D9 — no key/registry entry => owner-only => always
         refused here) and requires the session's ``mfa`` claim (D6)
         UNLESS the route is one of mfa.py's self-service exemptions
         (R4) — see ``admin_access_registry.MFA_SELF_SERVICE``.
      6. Otherwise => 403.
    """
    user = await current_dev(authorization)
    return await _finish_admin_check(user, request)


async def _finish_admin_check(user: dict, request=None) -> dict:
    if _legacy_admin_bypass(user):
        return user
    row_state = user.get("_row_lookup")
    if row_state == "db_error":
        raise HTTPException(503, "Could not confirm admin status — database unavailable")
    if row_state != "found":
        raise HTTPException(403, "Admin access required")

    from services.usage import is_founder_email
    email = (user.get("email") or "").strip().lower()
    if is_founder_email(email):
        _admin_decision.set(user)
        return user  # Owner — D1/D14, full access, no MFA claim required.

    from services import admin_team
    member = await admin_team.get_active_member(user.get("user_id"))
    if member is None:
        raise HTTPException(403, "Admin access required")

    from services.admin_access_registry import lookup_route, MFA_SELF_SERVICE, OWNER_ONLY
    route_key = _matched_route(request)
    mfa_info = MFA_SELF_SERVICE.get(route_key) if route_key else None
    if mfa_info is not None:
        if mfa_info["require_mfa_claim"] and not user.get("mfa"):
            raise HTTPException(403, "2FA session required")
        _admin_decision.set(user)
        return user

    entry = lookup_route(*route_key) if route_key else None
    if entry is None:
        # D9/D19 — no registry entry (or no matched route at all, e.g. an
        # inline caller that hasn't threaded `request` through) is treated
        # as owner-only. Only Owner (already returned above) passes here.
        raise HTTPException(403, "Admin access required")
    screen_key, level = entry
    if level == OWNER_ONLY or screen_key is None:
        raise HTTPException(403, "Admin access required")
    if not user.get("mfa"):
        raise HTTPException(403, "2FA session required")
    eff = await admin_team.effective_level(member, screen_key)
    if eff >= level:
        _admin_decision.set(user)
        return user
    raise HTTPException(403, "Admin access required")


async def require_admin_dep(request: Request, authorization: Optional[str] = Header(None)) -> dict:
    """Router-level admin dependency (defense-in-depth).

    Same rule as ``require_admin`` but shaped for FastAPI's
    ``APIRouter(dependencies=[Depends(require_admin_dep)])`` so EVERY
    route on an admin router is gated at the router boundary. A new
    admin endpoint added later inherits the gate automatically — it
    cannot accidentally ship unprotected even if the author forgets the
    inline ``await _require_admin(...)`` call.

    ``request: Request`` is REQUIRED here (not optional like on
    ``require_admin``) because Admin Team S2's route-registry lookup
    needs the matched route for every one of the ~24 router files using
    this as a router-level dependency — FastAPI injects it automatically,
    no edits needed to any of their individual route handlers. Stashed in
    `_current_request` (not passed as an explicit arg) so `require_admin`
    itself stays a plain, single-argument delegation.
    """
    _current_request.set(request)
    return await require_admin(authorization)


def create_token(user_id: str, email: str, is_admin: bool = False, mfa_verified: bool = False) -> str:
    """Create a signed JWT for a developer user.

    Iter 212m-48 — TTL shortened from 30 days to 7 days. The blast
    radius of a leaked token (XSS, stolen device, lost laptop) is now
    capped at one week. Active users get fresh tokens automatically
    via GET /auth/me, which re-signs on every call.

    Iter 212m-55 — `jti` (random 128-bit hex) + `iat` (issued-at)
    added. `jti` lets the server-side revocation list invalidate a
    specific leaked token without re-keying everyone. `iat` lets
    sensitive endpoints reject tokens older than a configurable
    replay window (e.g. admin actions). Backward-compatible: tokens
    without these claims still decode fine; only NEW tokens carry
    them.

    Admin Team S2 (AT1b) / D6 — `mfa_verified` sets the `mfa` claim,
    which the admin gate requires for a TEAM MEMBER (not the Owner) to
    use any non-self-service admin route. Only `routers/auth.py`'s
    `/auth/login/2fa-verify` success path (after a real TOTP/backup
    code check) and `/auth/me`'s token refresh (which preserves the
    INCOMING token's own `mfa` claim) pass `True`. Every other caller
    — signup, OAuth, the PIN-login flow (D6 explicitly out of scope),
    the existing workspace-Team invite-accept flow — keeps the default
    `False`, unchanged from before this parameter existed.
    """
    import uuid
    now = int(time.time())
    payload = {
        "user_id": user_id,
        "email": email,
        "is_admin": is_admin,
        "mfa": mfa_verified,
        "iat": now,                            # issued-at (replay window)
        "jti": uuid.uuid4().hex,               # unique token id (revocation)
        "exp": now + 86400 * 7,                # 7 days
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_mfa_pending_token(user_id: str, email: str) -> str:
    """Iter 212m-20 — short-lived JWT that ONLY carries the intent to
    complete a 2FA challenge. Cannot be used to call any other endpoint
    (the `mfa_pending=True` claim + 5-minute expiry are enforced by
    `consume_mfa_pending_token`). Returned by /auth/login when the
    admin's account has 2FA enabled; consumed by /auth/login/2fa-verify
    in exchange for the real session JWT."""
    payload = {
        "user_id":     user_id,
        "email":       email,
        "mfa_pending": True,
        "exp":         int(time.time()) + 5 * 60,   # 5 min window
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def consume_mfa_pending_token(token: str) -> dict:
    """Validate the mfa_pending token. Returns the payload on success,
    raises HTTPException(401) otherwise. The token is single-purpose —
    the caller MUST have already verified the 2FA code BEFORE issuing
    a real session token via `create_token`."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "2FA challenge expired — log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid 2FA token")
    if not payload.get("mfa_pending"):
        raise HTTPException(401, "Not a 2FA challenge token")
    if not payload.get("user_id"):
        raise HTTPException(401, "Malformed 2FA token")
    return payload
