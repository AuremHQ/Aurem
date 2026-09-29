"""
services/oauth_mcp_store.py — 2026-09-29 OAuth 2.1 for ORA Chat MCP.

Minimal, spec-compliant OAuth 2.1 authorization-server data layer for
ONE protected resource: the ORA Chat MCP endpoint
(routers/mcp_ora_chat.py, POST /api/aurem-dev/mcp/ora-chat).

Design (per integration playbook + MCP authorization spec):
  - Dynamic Client Registration (RFC 7591) — any MCP client (Claude
    Desktop custom connectors) can self-register. Registrations are
    UNTRUSTED — they only get a client_id/redirect_uris; the actual
    admin login + consent step is what gates real access.
  - Authorization Code + PKCE (S256 only, RFC 7636) — no "plain".
  - Opaque, hashed, DB-backed bearer tokens (NOT JWTs) — same
    trade-off already used for services/admin_gateway_keys.py:
    trivial revocation, no signing-key rotation to manage.
  - Refresh tokens rotate on every use; reuse of an already-used
    refresh token revokes the whole token family (theft detection).
  - "Login limited to admin accounts" is enforced by (a) the
    /oauth/authorize → /oauth/pending → /oauth/authorize/approve
    flow requiring a real ORA admin JWT (routers/oauth_mcp.py uses
    `require_admin`), and (b) `verify_access_token` re-checking the
    token's owning user is STILL an admin on every single MCP call —
    identical live-recheck rule as `admin_gateway_keys.verify_key`.

Five Mongo collections, all TTL-cleaned (expiry is still re-checked in
code — the TTL index is cleanup, not the authorization boundary):
  oauth_mcp_clients        — DCR-registered clients
  oauth_mcp_pending         — one row per in-flight /oauth/authorize
                              request, keyed by `consent_id`
  oauth_mcp_auth_codes      — one-time authorization codes
  oauth_mcp_access_tokens   — opaque access tokens (hash only)
  oauth_mcp_refresh_tokens  — opaque refresh tokens (hash only)
"""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Optional

from cto_services.db import get_db

MCP_RESOURCE_PATH = "/api/aurem-dev/mcp/ora-chat"
ISSUER_PATH = "/api/aurem-dev/mcp"

PENDING_TTL_S = 10 * 60
CODE_TTL_S = 5 * 60
ACCESS_TOKEN_TTL_S = 60 * 60
REFRESH_TOKEN_TTL_S = 30 * 24 * 60 * 60

_TOKEN_PREFIX = "oraoauth_at_"
_REFRESH_PREFIX = "oraoauth_rt_"

_indexes_ready = False


async def _ensure_indexes(db) -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    try:
        await db.oauth_mcp_pending.create_index("expires_at", expireAfterSeconds=0)
        await db.oauth_mcp_auth_codes.create_index("expires_at", expireAfterSeconds=0)
        await db.oauth_mcp_access_tokens.create_index("expires_at", expireAfterSeconds=0)
        await db.oauth_mcp_refresh_tokens.create_index("expires_at", expireAfterSeconds=0)
        await db.oauth_mcp_clients.create_index("client_id", unique=True)
    except Exception:  # noqa: BLE001 — index creation is best-effort
        pass
    _indexes_ready = True


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _valid_redirect_uri(uri: str) -> bool:
    if not isinstance(uri, str) or not uri:
        return False
    if uri.startswith("https://"):
        return True
    return uri.startswith("http://localhost:") or uri.startswith("http://127.0.0.1:")


def resource_path_matches(resource: str) -> bool:
    """Validate the `resource` param (RFC 8707) targets exactly our
    one protected resource, regardless of scheme/host (those vary
    between preview pods and auremcto.com)."""
    if not isinstance(resource, str) or not resource:
        return False
    try:
        from urllib.parse import urlparse
        return urlparse(resource).path.rstrip("/") == MCP_RESOURCE_PATH
    except Exception:
        return False


# ── Dynamic Client Registration (RFC 7591) ──────────────────────────

async def register_client(client_name: str, redirect_uris: list,
                           token_endpoint_auth_method: str = "none") -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    await _ensure_indexes(db)
    if not redirect_uris or not isinstance(redirect_uris, list) or len(redirect_uris) > 10:
        raise ValueError("invalid_client_metadata: redirect_uris required (max 10)")
    for uri in redirect_uris:
        if not _valid_redirect_uri(uri):
            raise ValueError(f"invalid_redirect_uri: {uri!r} must be https:// or http://localhost")
    if token_endpoint_auth_method not in ("none", "client_secret_basic"):
        raise ValueError("invalid_client_metadata: unsupported token_endpoint_auth_method")
    client_id = secrets.token_urlsafe(24)
    now = time.time()
    doc = {
        "client_id": client_id,
        "client_name": (client_name or "Unnamed MCP client").strip()[:120],
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": token_endpoint_auth_method,
        "created_at": now,
        "client_secret_hash": None,
    }
    out = {
        "client_id": client_id,
        "client_id_issued_at": int(now),
        "client_name": doc["client_name"],
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": token_endpoint_auth_method,
    }
    if token_endpoint_auth_method == "client_secret_basic":
        secret = secrets.token_urlsafe(32)
        doc["client_secret_hash"] = _hash(secret)
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    await db.oauth_mcp_clients.insert_one(doc)
    return out


async def get_client(client_id: str) -> Optional[dict]:
    db = get_db()
    if db is None:
        return None
    doc = await db.oauth_mcp_clients.find_one({"client_id": client_id}, {"_id": 0})
    return doc


async def verify_client_secret(client_id: str, client_secret: Optional[str]) -> Optional[dict]:
    client = await get_client(client_id)
    if not client:
        return None
    if client.get("token_endpoint_auth_method") == "client_secret_basic":
        if not client_secret or _hash(client_secret) != client.get("client_secret_hash"):
            return None
    return client


# ── Pending /oauth/authorize requests (server-side, not trusted from
#    round-tripped query params) ─────────────────────────────────────

async def create_pending(*, client_id: str, client_name: str, redirect_uri: str,
                          code_challenge: str, resource: str, scope: str,
                          state: Optional[str]) -> str:
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    await _ensure_indexes(db)
    consent_id = secrets.token_urlsafe(24)
    now = time.time()
    await db.oauth_mcp_pending.insert_one({
        "consent_id": consent_id,
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "resource": resource,
        "scope": scope,
        "state": state,
        "created_at": now,
        "expires_at": now + PENDING_TTL_S,
    })
    return consent_id


async def get_pending(consent_id: str) -> Optional[dict]:
    db = get_db()
    if db is None:
        return None
    doc = await db.oauth_mcp_pending.find_one(
        {"consent_id": consent_id, "expires_at": {"$gt": time.time()}}, {"_id": 0},
    )
    return doc


async def delete_pending(consent_id: str) -> None:
    db = get_db()
    if db is None:
        return
    await db.oauth_mcp_pending.delete_one({"consent_id": consent_id})


# ── Authorization codes ──────────────────────────────────────────────

async def create_auth_code(pending: dict, admin_user_id: str) -> str:
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    code = secrets.token_urlsafe(32)
    now = time.time()
    await db.oauth_mcp_auth_codes.insert_one({
        "code_hash": _hash(code),
        "client_id": pending["client_id"],
        "redirect_uri": pending["redirect_uri"],
        "code_challenge": pending["code_challenge"],
        "resource": pending["resource"],
        "scope": pending["scope"],
        "admin_user_id": admin_user_id,
        "used": False,
        "created_at": now,
        "expires_at": now + CODE_TTL_S,
    })
    return code


def _pkce_matches(code_verifier: str, code_challenge: str) -> bool:
    import base64
    if not code_verifier:
        return False
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge) if hasattr(secrets, "compare_digest") \
        else computed == code_challenge


async def consume_auth_code(code: str, client_id: str, redirect_uri: str,
                             code_verifier: str) -> Optional[dict]:
    """Atomically marks the code used. Returns the code row (with
    admin_user_id/resource/scope) on success, None on ANY mismatch —
    wrong client, wrong redirect_uri, expired, already used, or bad
    PKCE verifier. Never distinguishes WHY to the caller (avoids
    leaking which check failed)."""
    db = get_db()
    if db is None:
        return None
    row = await db.oauth_mcp_auth_codes.find_one_and_update(
        {
            "code_hash": _hash(code), "client_id": client_id,
            "redirect_uri": redirect_uri, "used": False,
            "expires_at": {"$gt": time.time()},
        },
        {"$set": {"used": True}},
    )
    if not row:
        return None
    if not _pkce_matches(code_verifier, row["code_challenge"]):
        return None
    return row


# ── Access / refresh tokens ──────────────────────────────────────────

async def issue_token_pair(*, client_id: str, admin_user_id: str, resource: str,
                            scope: str, family_id: Optional[str] = None) -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    await _ensure_indexes(db)
    now = time.time()
    access_raw = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    refresh_raw = _REFRESH_PREFIX + secrets.token_urlsafe(32)
    family_id = family_id or secrets.token_hex(12)
    await db.oauth_mcp_access_tokens.insert_one({
        "token_hash": _hash(access_raw), "client_id": client_id,
        "admin_user_id": admin_user_id, "resource": resource, "scope": scope,
        "created_at": now, "expires_at": now + ACCESS_TOKEN_TTL_S, "revoked": False,
    })
    await db.oauth_mcp_refresh_tokens.insert_one({
        "token_hash": _hash(refresh_raw), "client_id": client_id,
        "admin_user_id": admin_user_id, "resource": resource, "scope": scope,
        "family_id": family_id, "used": False,
        "created_at": now, "expires_at": now + REFRESH_TOKEN_TTL_S,
    })
    return {
        "access_token": access_raw, "refresh_token": refresh_raw,
        "token_type": "Bearer", "expires_in": ACCESS_TOKEN_TTL_S, "scope": scope,
    }


_last_used_write: dict[str, float] = {}
_TOUCH_THROTTLE_S = 20.0


async def verify_access_token(raw_token: Optional[str]) -> Optional[dict]:
    """Look up an OAuth access token, confirm not expired/revoked, the
    resource it was scoped to is OUR MCP resource, and the owning user
    is STILL an admin right now (live re-check — same rule as
    admin_gateway_keys.verify_key). Returns a normalized dict shaped
    like an admin_gateway_keys doc so routers/mcp_ora_chat.py's
    downstream rate-limit / spend-cap / usage-log code works
    unchanged for BOTH auth paths."""
    if not raw_token or not raw_token.startswith(_TOKEN_PREFIX):
        return None
    db = get_db()
    if db is None:
        return None
    doc = await db.oauth_mcp_access_tokens.find_one({
        "token_hash": _hash(raw_token), "revoked": {"$ne": True},
        "expires_at": {"$gt": time.time()},
    })
    if not doc:
        return None
    if not resource_path_matches(doc.get("resource", "")):
        return None
    owner = await db.dev_users.find_one(
        {"user_id": doc["admin_user_id"]}, {"is_admin": 1, "tier": 1},
    )
    if not owner or not (owner.get("is_admin") or owner.get("tier") == "founder"):
        return None
    synthetic_key_id = f"oauth_{doc['client_id']}"
    now = time.monotonic()
    last = _last_used_write.get(synthetic_key_id)
    if last is None or (now - last) >= _TOUCH_THROTTLE_S:
        _last_used_write[synthetic_key_id] = now
        await db.oauth_mcp_access_tokens.update_one(
            {"token_hash": doc["token_hash"]}, {"$set": {"last_used_at": time.time()}},
        )
    from services.admin_gateway_keys import DEFAULT_RATE_LIMIT_PER_MIN, DEFAULT_SPEND_CAP_USD
    return {
        "key_id": synthetic_key_id,
        "created_by_user_id": doc["admin_user_id"],
        "rate_limit_per_min": DEFAULT_RATE_LIMIT_PER_MIN,
        "spend_cap_monthly_usd": DEFAULT_SPEND_CAP_USD,
        "auth_kind": "oauth",
        "client_id": doc["client_id"],
    }


async def rotate_refresh_token(raw_refresh: str, client_id: str) -> Optional[dict]:
    """Consume a refresh token and issue a fresh pair. Detects reuse
    (an already-`used` token being presented again) and, on reuse,
    revokes the ENTIRE token family — standard refresh-token-theft
    mitigation."""
    db = get_db()
    if db is None:
        return None
    token_hash = _hash(raw_refresh)
    row = await db.oauth_mcp_refresh_tokens.find_one(
        {"token_hash": token_hash, "client_id": client_id},
    )
    if not row:
        return None
    if row.get("used"):
        # Reuse detected — kill the whole family.
        await db.oauth_mcp_refresh_tokens.update_many(
            {"family_id": row["family_id"]}, {"$set": {"used": True}},
        )
        await db.oauth_mcp_access_tokens.update_many(
            {"client_id": client_id, "admin_user_id": row["admin_user_id"],
             "resource": row["resource"]},
            {"$set": {"revoked": True}},
        )
        return None
    if row["expires_at"] <= time.time():
        return None
    await db.oauth_mcp_refresh_tokens.update_one(
        {"token_hash": token_hash}, {"$set": {"used": True}},
    )
    return await issue_token_pair(
        client_id=client_id, admin_user_id=row["admin_user_id"],
        resource=row["resource"], scope=row["scope"], family_id=row["family_id"],
    )


# ── Admin visibility (used by routers/admin_gateway.py) ─────────────

async def list_oauth_clients() -> list[dict]:
    db = get_db()
    if db is None:
        return []
    out = []
    cursor = db.oauth_mcp_clients.find({}, {"_id": 0, "client_secret_hash": 0}).sort("created_at", -1)
    async for c in cursor:
        active_tokens = await db.oauth_mcp_access_tokens.count_documents(
            {"client_id": c["client_id"], "revoked": {"$ne": True}, "expires_at": {"$gt": time.time()}},
        )
        c["active_access_tokens"] = active_tokens
        out.append(c)
    return out


async def revoke_client(client_id: str) -> bool:
    db = get_db()
    if db is None:
        return False
    await db.oauth_mcp_access_tokens.update_many(
        {"client_id": client_id}, {"$set": {"revoked": True}},
    )
    await db.oauth_mcp_refresh_tokens.update_many(
        {"client_id": client_id}, {"$set": {"used": True}},
    )
    r = await db.oauth_mcp_clients.delete_one({"client_id": client_id})
    return r.deleted_count > 0
