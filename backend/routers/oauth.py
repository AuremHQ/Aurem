"""
routers/oauth.py — OAuth 2.1 + PKCE, shared by the MCP "Claude
Directory" listing (routers/mcp.py, any dev_user) AND the admin-only
ORA Chat MCP server (routers/mcp_ora_chat.py, resource=
/api/aurem-dev/mcp/ora-chat — gated to admin accounts only, see
MCP_ORA_CHAT_RESOURCE_PATH below).

Endpoints (mounted at /api/aurem-dev, PLUS root-level `.well-known/*`
aliases registered in main.py — Claude's MCP client, and RFC 8414/9728
generally, look for discovery docs at the bare domain root first):
  GET  /.well-known/oauth-authorization-server  — RFC 8414 discovery
  GET  /.well-known/oauth-protected-resource     — RFC 9728 (ORA Chat MCP)
  POST /oauth/register                           — RFC 7591 Dynamic Client Registration
  GET  /oauth/authorize                         — consent / login HTML
  POST /oauth/authorize                         — submit credentials → issue code
  POST /oauth/authorize/deny                    — user cancelled
  POST /oauth/token                             — exchange code → access token (PKCE-verified)
  GET  /oauth/userinfo                          — token introspection / sub claim

Flow (RFC 6749 §4.1 + RFC 7636 S256):
  1. Client (self-registers via /oauth/register, or uses a
     manually-configured client_id) hits /oauth/authorize with
     ?response_type=code&client_id=&redirect_uri=&scope=mcp&state=
     &code_challenge=&code_challenge_method=S256&resource=
  2. We render a login form. User submits email + password. If
     `resource` targets the ORA Chat MCP resource specifically, the
     user must be an ORA admin or the flow is rejected right here
     (error=admin_required) — every OTHER resource on this shared
     server stays open to any dev_user, unchanged.
  3. We verify creds with bcrypt (mirroring routers/auth.login), issue a short-lived
     auth code (10 min TTL), and 302 back to the client's redirect_uri with ?code=&state=
  4. Client POSTs /oauth/token with grant_type=authorization_code, code, code_verifier
  5. We verify the PKCE challenge, mark the code used, and issue an `sk-aurem-oauth-*`
     access token in db.api_keys (same collection mcp.py already consumes via
     _resolve_user → so the token works for MCP calls immediately, no second wiring).
     `resource` is stamped onto the token so routers/mcp_ora_chat.py's
     verify_oauth_token_for_resource() can confirm a token presented
     there was actually issued FOR the ORA Chat MCP resource, not some
     other API surface.

Token TTL: 30 days. Auth code TTL: 10 min. Both enforced both by `expires_at`
check at use time AND a Mongo TTL index created in main.py lifespan so the
collections self-clean.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.rate_limiter import check_rate_limit_async

logger = logging.getLogger(__name__)
router = APIRouter(tags=["OAuth"])

AUTH_CODE_TTL = 600                   # 10 min
TOKEN_TTL     = 60 * 60 * 24 * 30     # 30 days
API_KEY_PREFIX = "sk-aurem-oauth-"    # distinct from manual sk-aurem-* keys
DCR_RATE_LIMIT_PER_MIN = 10

# 2026-09-30 — the ORA Chat MCP server (routers/mcp_ora_chat.py) is the
# ONE resource on this shared OAuth 2.1 server that must be admin-only.
# Every other existing consumer (the general MCP "Claude Directory"
# listing, routers/mcp.py) stays open to any dev_user, unchanged.
MCP_ORA_CHAT_RESOURCE_PATH = "/api/aurem-dev/mcp/ora-chat"

# 2026-09-30 — SECURITY FIX (real High-severity finding, code-read audit):
# the ONLY pre-existing, non-DCR client this server has ever actually
# talked to is Claude Desktop/Web's own custom-connector flow, whose
# callback is a fixed, Anthropic-owned, non-attacker-controllable host
# (https://claude.com/docs/connectors/building/authentication). This is
# the ONE legacy exemption from "every redirect_uri must be registered"
# below — anything else must go through /oauth/register (RFC 7591) first.
LEGACY_CLAUDE_REDIRECT_HOST = "claude.ai"


def _redirect_uri_host(uri: str) -> Optional[str]:
    """Exact scheme+host parse — NEVER use string prefix matching
    (startswith) on a URL for a security decision. `http://localhost:1@evil.com/`
    starts with `http://localhost:` but its actual host, per every URL
    parser and every browser, is `evil.com` (the `localhost:1` segment
    is userinfo — user:password — not the host)."""
    if not isinstance(uri, str) or not uri:
        return None
    # Python's URL parser and a browser disagree on some inputs: for
    # `https://evil.example\\@claude.ai/` urlsplit reports host `claude.ai`
    # while a browser treats the backslash as a slash and goes to
    # `evil.example`. Refuse anything a parser could read two ways instead of
    # relying on how a library happens to encode it.
    if "\\" in uri or "%5c" in uri.lower() or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in uri):
        return None
    try:
        parsed = urllib.parse.urlsplit(uri)
        parsed.port  # noqa: B018 — raises ValueError on a malformed port
    except ValueError:
        return None
    if parsed.fragment or not parsed.scheme or not parsed.hostname:
        return None
    # Legitimate OAuth callbacks never carry userinfo; `user@host` is only
    # ever used to make one host look like another.
    if parsed.username is not None or parsed.password is not None:
        return None
    return parsed.hostname


def _is_legacy_claude_redirect_uri(uri: str) -> bool:
    parsed_scheme = urllib.parse.urlsplit(uri).scheme if isinstance(uri, str) else ""
    return parsed_scheme == "https" and _redirect_uri_host(uri) == LEGACY_CLAUDE_REDIRECT_HOST


def _valid_redirect_uri(uri: str) -> bool:
    """Registration-time policy only (used by /oauth/register). Exact
    per-request authorization is done by _validate_redirect_uri_for_client
    below, which additionally requires the URI to be one this specific
    client actually registered (or the legacy Claude exemption)."""
    host = _redirect_uri_host(uri)
    if host is None:
        return False
    scheme = urllib.parse.urlsplit(uri).scheme
    if scheme == "https":
        return True
    return scheme == "http" and host in ("localhost", "127.0.0.1")


def _resource_path(resource: str) -> str:
    if not resource:
        return ""
    try:
        from urllib.parse import urlparse
        return urlparse(resource).path.rstrip("/")
    except Exception:
        return ""


def _public_base() -> str:
    """Issuer base URL. Strips trailing slash. Defaults to prod."""
    return os.getenv("AUREM_PUBLIC_BASE_URL", "https://auremcto.com").rstrip("/")


def _issuer() -> str:
    """Issuer claim for the discovery doc — root of the API namespace."""
    return f"{_public_base()}/api/aurem-dev"


# ── Discovery (RFC 8414) ──────────────────────────────────────────────

@router.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    base = _issuer()
    return {
        "issuer": base,
        "authorization_endpoint":            f"{base}/oauth/authorize",
        "token_endpoint":                    f"{base}/oauth/token",
        "registration_endpoint":             f"{base}/oauth/register",
        "userinfo_endpoint":                 f"{base}/oauth/userinfo",
        "response_types_supported":          ["code"],
        "grant_types_supported":             ["authorization_code"],
        "code_challenge_methods_supported":  ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported":                  ["openid", "profile", "mcp"],
    }


# Exported so main.py can mount the same handler at the domain-root
# `/.well-known/oauth-authorization-server` path — Claude's MCP client
# (and RFC 8414-compliant clients generally) looks there FIRST, not
# under /api/aurem-dev.
async def oauth_discovery_root(_request):
    return JSONResponse(await oauth_discovery())


# OIDC-style alias — some clients probe this path instead of/before
# the RFC 8414 one. Identical content; we're not a real OIDC provider
# (no id_token), but there's no harm in answering here too.
async def openid_configuration_root(_request):
    return JSONResponse(await oauth_discovery())


@router.get("/.well-known/openid-configuration")
async def openid_configuration():
    return await oauth_discovery()


@router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource():
    """RFC 9728 — declares the ORA Chat MCP server as a protected
    resource of THIS authorization server. (Previously a stale static
    frontend file with `authorization_servers: []` — nothing pointed
    at a real AS, so no client could ever complete discovery.)"""
    return {
        "resource": f"{_public_base()}{MCP_ORA_CHAT_RESOURCE_PATH}",
        "authorization_servers": [_issuer()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
        "resource_name": "ORA Chat MCP (admin only)",
    }


async def oauth_protected_resource_root(_request):
    return JSONResponse(await oauth_protected_resource())


# ── Dynamic Client Registration (RFC 7591) ────────────────────────────

@router.post("/oauth/register", status_code=201)
async def oauth_register(request: Request):
    """Any MCP client (Claude Desktop custom connectors, etc.) can
    self-register. This is intentionally UNTRUSTED at registration
    time — real access is still gated by a live admin login + consent
    at /oauth/authorize for admin-only resources like ORA Chat MCP."""
    fwd = request.headers.get("x-forwarded-for")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")
    if not await check_rate_limit_async(f"oauth_dcr:{ip}", DCR_RATE_LIMIT_PER_MIN):
        raise HTTPException(429, "Too many registration attempts. Try again shortly.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid_client_metadata: body must be JSON")
    redirect_uris = body.get("redirect_uris")
    if not redirect_uris or not isinstance(redirect_uris, list) or len(redirect_uris) > 10:
        raise HTTPException(400, "invalid_client_metadata: redirect_uris required (max 10)")
    for uri in redirect_uris:
        if not _valid_redirect_uri(uri):
            raise HTTPException(400, f"invalid_redirect_uri: {uri!r} must be https:// or http://localhost")
    token_endpoint_auth_method = body.get("token_endpoint_auth_method", "none")
    if token_endpoint_auth_method not in ("none", "client_secret_post"):
        raise HTTPException(400, "invalid_client_metadata: unsupported token_endpoint_auth_method")

    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    client_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    doc = {
        "client_id": client_id,
        "client_name": str(body.get("client_name") or "Unnamed MCP client")[:120],
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": token_endpoint_auth_method,
        "created_at": now,
        "client_secret_hash": None,
    }
    out = {
        "client_id": client_id,
        "client_id_issued_at": int(now.timestamp()),
        "client_name": doc["client_name"],
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": token_endpoint_auth_method,
    }
    if token_endpoint_auth_method == "client_secret_post":
        secret = secrets.token_urlsafe(32)
        doc["client_secret_hash"] = hashlib.sha256(secret.encode()).hexdigest()
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    await db.oauth_dcr_clients.insert_one(doc)
    logger.info(f"[oauth] DCR registered client_id={client_id} name={doc['client_name']!r}")
    return JSONResponse(out, status_code=201)


async def _get_dcr_client(client_id: str) -> Optional[dict]:
    db = get_db()
    if db is None or not client_id:
        return None
    return await db.oauth_dcr_clients.find_one({"client_id": client_id}, {"_id": 0})


async def _validate_redirect_uri_for_client(
    client_id: str, redirect_uri: str, resource_path: str,
) -> tuple[bool, str, Optional[dict]]:
    """The core fix for the High-severity finding: every redirect_uri
    must now be either (a) the fixed, Anthropic-owned Claude callback
    host, or (b) one this exact client_id actually registered via
    /oauth/register. A client_id with NO registration on file and a
    redirect_uri that isn't the Claude exemption is rejected outright —
    previously it was silently allowed through with zero checking.

    Returns (ok, error_code, dcr_client_or_None). error_code is only
    meaningful when ok is False: "invalid_redirect_uri" (host itself is
    malformed/not https/not localhost — same policy as registration) or
    "unregistered_client" (well-formed URI, but this client never
    registered it).

    Callers MUST treat ok=False as "render a local error, do not
    redirect" (OAuth 2.1 requires this — see playbook §5): the whole
    point of this function is that we do not yet know whether
    redirect_uri is safe to send the user's browser to.
    """
    if not _valid_redirect_uri(redirect_uri):
        return False, "invalid_redirect_uri", None

    # ORA Chat MCP is the ONE admin-only, tool-executing resource on
    # this shared server. Per founder decision: only the real Claude
    # callback may ever receive a code for it, DCR or not — DCR's
    # "any https:// URI" policy is deliberately NOT trusted here, since
    # an attacker can self-register a client the normal way and still
    # get an admin's auth code redirected to their own https:// server.
    if resource_path == MCP_ORA_CHAT_RESOURCE_PATH:
        if _is_legacy_claude_redirect_uri(redirect_uri):
            return True, "", await _get_dcr_client(client_id)
        return False, "unregistered_client", None

    if _is_legacy_claude_redirect_uri(redirect_uri):
        return True, "", await _get_dcr_client(client_id)

    dcr_client = await _get_dcr_client(client_id)
    if not dcr_client:
        return False, "unregistered_client", None
    registered = dcr_client.get("redirect_uris") or []
    if not any(secrets.compare_digest(redirect_uri, r) for r in registered):
        return False, "unregistered_client", None
    return True, "", dcr_client


async def list_ora_chat_oauth_clients() -> list[dict]:
    """Admin-panel visibility: DCR-registered clients that have
    actually obtained a token scoped to the ORA Chat MCP resource
    (registering alone doesn't grant anything — this is what's
    ACTUALLY connected)."""
    db = get_db()
    if db is None:
        return []
    client_ids = await db.api_keys.distinct(
        "client_id", {"source": "oauth", "resource": MCP_ORA_CHAT_RESOURCE_PATH},
    )
    out = []
    for cid in client_ids:
        if not cid or cid == "unknown":
            continue
        dcr = await db.oauth_dcr_clients.find_one({"client_id": cid}, {"_id": 0})
        active = await db.api_keys.count_documents({
            "client_id": cid, "source": "oauth", "resource": MCP_ORA_CHAT_RESOURCE_PATH,
            "active": True,
        })
        out.append({
            "client_id": cid,
            "client_name": (dcr or {}).get("client_name", cid[:24]),
            "active_access_tokens": active,
        })
    return out


async def revoke_ora_chat_oauth_client(client_id: str) -> bool:
    db = get_db()
    if db is None:
        return False
    r = await db.api_keys.update_many(
        {"client_id": client_id, "source": "oauth", "resource": MCP_ORA_CHAT_RESOURCE_PATH},
        {"$set": {"active": False}},
    )
    await db.oauth_dcr_clients.delete_one({"client_id": client_id})
    return r.matched_count > 0


async def verify_oauth_token_for_resource(raw_token: str, resource_path: str) -> Optional[dict]:
    """Used by routers/mcp_ora_chat.py to accept `sk-aurem-oauth-*`
    tokens minted by THIS shared OAuth server, in addition to its own
    static admin keys. Confirms the token was actually issued for
    `resource_path` (not some other API surface), is active/
    unexpired, and — since this check is only ever called for the
    admin-only ORA Chat MCP resource — that the owning user is STILL
    an admin right now (live re-check, same rule as every other admin
    credential in this app)."""
    if not raw_token or not raw_token.startswith(API_KEY_PREFIX):
        return None
    db = get_db()
    if db is None:
        return None
    doc = await db.api_keys.find_one({"key": raw_token, "active": True})
    if not doc:
        return None
    exp = doc.get("expires_at")
    if isinstance(exp, datetime):
        exp_dt = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp_dt:
            return None
    if _resource_path(doc.get("resource", "")) != resource_path:
        return None
    user = await db.dev_users.find_one({"user_id": doc["user_id"]}, {"is_admin": 1, "tier": 1})
    if not user or not (user.get("is_admin") or user.get("tier") == "founder"):
        return None
    return {
        "key_id": f"oauth_{doc.get('client_id', 'unknown')}",
        "created_by_user_id": doc["user_id"],
        "rate_limit_per_min": 60,
        "spend_cap_monthly_usd": 50,
        "auth_kind": "oauth",
    }


# ── Authorization endpoint (GET — consent page) ───────────────────────

@router.get("/oauth/authorize", response_class=HTMLResponse)
async def oauth_authorize_page(
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "mcp",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    resource: str = "",
    error: str = "",
):
    if response_type != "code":
        raise HTTPException(400, "unsupported_response_type — only 'code' is supported")
    if code_challenge_method != "S256":
        raise HTTPException(400, "unsupported_code_challenge_method — only 'S256' is supported")
    if not redirect_uri:
        raise HTTPException(400, "missing redirect_uri")
    if not code_challenge:
        raise HTTPException(400, "missing code_challenge (PKCE required)")

    # 2026-09-30 — SECURITY FIX: validate client_id/redirect_uri/resource
    # BEFORE ever rendering the consent page (never touch the login
    # form for a request we can't tell is safe to redirect back from).
    # A local 400 here, never a redirect — see _validate_redirect_uri_for_client.
    resource_path = _resource_path(resource)
    ok, err_code, dcr_client = await _validate_redirect_uri_for_client(client_id, redirect_uri, resource_path)
    if not ok:
        raise HTTPException(400, f"{err_code}: redirect_uri is not registered for this client_id")

    params = urllib.parse.urlencode({
        "response_type":         response_type,
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "scope":                 scope,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": code_challenge_method,
        "resource":              resource,
    })

    if error == "admin_required":
        err_block = (
            '<div style="background:#7f1d1d;color:#fecaca;padding:10px 14px;'
            'border-radius:8px;font-size:13px;margin-bottom:14px">'
            'This connection is restricted to ORA admin accounts. '
            'Sign in with an admin account to continue.</div>'
        )
    elif error == "invalid_credentials":
        err_block = (
            '<div style="background:#7f1d1d;color:#fecaca;padding:10px 14px;'
            'border-radius:8px;font-size:13px;margin-bottom:14px">'
            'Invalid email or password. Please try again.</div>'
        )
    else:
        err_block = ""

    # 2026-09-30 — SECURITY FIX: show the ACTUAL registered client name
    # (never the raw, attacker-controlled client_id string) plus the
    # real redirect host, so a user asked to type their password can
    # see exactly who is asking and where they'll be sent back to —
    # the two things they'd need to notice a phishing attempt.
    if _is_legacy_claude_redirect_uri(redirect_uri):
        display_client_name = (dcr_client or {}).get("client_name") or "Claude"
    else:
        display_client_name = (dcr_client or {}).get("client_name") or "Unknown client"
    safe_client_name = str(display_client_name)[:80].replace("<", "&lt;").replace(">", "&gt;")
    safe_redirect_host = str(_redirect_uri_host(redirect_uri) or "unknown host").replace("<", "&lt;").replace(">", "&gt;")
    # Defensive HTML-escape: scope rendered for transparency.
    safe_scope = (scope or "mcp").replace("<", "&lt;").replace(">", "&gt;")

    if resource_path == MCP_ORA_CHAT_RESOURCE_PATH:
        scope_rows = (
            '<div class="scope-row"><span class="check">✓</span> Ask ORA Chat questions</div>'
            '<div class="scope-row"><span class="check">✓</span> View your ORA Chat conversations</div>'
            '<div class="scope-row" style="color:#f59e0b">'
            '<span class="check" style="color:#f59e0b">⚑</span> ADMIN-ONLY — only ORA admin '
            'accounts can authorize this connection</div>'
        )
    else:
        scope_rows = (
            '<div class="scope-row"><span class="check">✓</span> List your projects</div>'
            '<div class="scope-row"><span class="check">✓</span> Ship code tasks via MCP</div>'
            '<div class="scope-row"><span class="check">✓</span> View task status</div>'
            '<div class="scope-row"><span class="check">✓</span> View recent commits</div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Connect ORA to Claude — Authorize Access</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: radial-gradient(ellipse at top, #0f172a 0%, #020617 70%);
      color: #f1f5f9;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 24px;
    }}
    .card {{
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 16px;
      padding: 36px 32px;
      max-width: 420px;
      width: 100%;
      box-shadow: 0 24px 60px rgba(0,0,0,0.5);
    }}
    .logo {{
      width: 64px;
      height: 64px;
      border-radius: 16px;
      background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #000;
      font-weight: 900;
      font-size: 22px;
      letter-spacing: -0.5px;
      margin-bottom: 22px;
    }}
    h1 {{ color: #f59e0b; font-size: 22px; margin: 0 0 6px; font-weight: 700; }}
    p.lead {{ color: #94a3b8; font-size: 14px; line-height: 1.6; margin: 0 0 18px; }}
    .scopes {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 10px;
      padding: 14px 16px;
      margin: 0 0 20px;
      font-size: 13px;
      color: #cbd5e1;
      line-height: 1.9;
    }}
    .scope-row {{ display: flex; gap: 8px; }}
    .check {{ color: #22c55e; font-weight: 700; }}
    label {{ display: block; font-size: 12px; color: #94a3b8; margin: 12px 0 4px; }}
    input {{
      width: 100%;
      padding: 11px 12px;
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 8px;
      color: #f1f5f9;
      font-size: 14px;
      transition: border 0.15s;
    }}
    input:focus {{ outline: none; border-color: #f59e0b; }}
    .btn {{
      width: 100%;
      padding: 13px;
      border-radius: 10px;
      border: none;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 14px;
      transition: transform 0.08s, opacity 0.15s;
    }}
    .btn:active {{ transform: translateY(1px); }}
    .btn-primary {{
      background: #f59e0b;
      color: #0a0e1a;
    }}
    .btn-primary:hover {{ opacity: 0.92; }}
    .btn-secondary {{
      background: transparent;
      border: 1px solid #334155;
      color: #94a3b8;
    }}
    .btn-secondary:hover {{ border-color: #475569; color: #cbd5e1; }}
    .footer {{ font-size: 11px; color: #475569; margin-top: 18px; text-align: center; }}
    .footer code {{ background: #1e293b; padding: 1px 5px; border-radius: 3px; color: #cbd5e1; }}
  </style>
</head>
<body>
  <div class="card" data-testid="oauth-consent-card">
    <div class="logo">ORA</div>
    <h1>Connect ORA to Claude</h1>
    <p class="lead"><b data-testid="oauth-client-name">{safe_client_name}</b> is requesting access to your ORA by Aurem account.
    You'll be redirected back to <b data-testid="oauth-redirect-host">{safe_redirect_host}</b> after you allow or deny this.</p>
    {err_block}
    <div class="scopes">
      {scope_rows}
    </div>
    <form method="POST" action="/api/aurem-dev/oauth/authorize?{params}" data-testid="oauth-login-form">
      <label for="email">ORA email</label>
      <input id="email" type="email" name="email" required autocomplete="email"
             data-testid="oauth-email" />
      <label for="password">Password</label>
      <input id="password" type="password" name="password" required autocomplete="current-password"
             data-testid="oauth-password" />
      <button class="btn btn-primary" type="submit" data-testid="oauth-authorize-btn">
        Authorize ORA Access
      </button>
    </form>
    <form method="POST" action="/api/aurem-dev/oauth/authorize/deny?{params}">
      <button class="btn btn-secondary" type="submit" data-testid="oauth-deny-btn">
        Cancel
      </button>
    </form>
    <div class="footer">
      Scope requested: <code>{safe_scope}</code> · Powered by OAuth 2.1 + PKCE
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


# ── Authorization endpoint (POST — process consent) ───────────────────

@router.post("/oauth/authorize")
async def oauth_authorize_submit(
    request: Request,
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "mcp",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    resource: str = "",
    email: str = Form(""),
    password: str = Form(""),
):
    """Verify creds (bcrypt — mirrors routers/auth.login), then issue auth code."""
    if not redirect_uri or not code_challenge:
        raise HTTPException(400, "missing redirect_uri or code_challenge")

    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    # 2026-09-30 — SECURITY FIX: re-validate here too (defense in depth
    # against a POST sent directly, bypassing the GET page's own check
    # above). A rejection here raises a LOCAL error, never a redirect —
    # we cannot safely redirect anywhere until this passes.
    resource_path = _resource_path(resource)
    ok, err_code, _dcr_client = await _validate_redirect_uri_for_client(client_id, redirect_uri, resource_path)
    if not ok:
        raise HTTPException(400, f"{err_code}: redirect_uri is not registered for this client_id")

    def _reshow(error_code: str) -> RedirectResponse:
        qs = urllib.parse.urlencode({
            "response_type":         response_type,
            "client_id":             client_id,
            "redirect_uri":          redirect_uri,
            "scope":                 scope,
            "state":                 state,
            "code_challenge":        code_challenge,
            "code_challenge_method": code_challenge_method,
            "resource":              resource,
            "error":                 error_code,
        })
        return RedirectResponse(url=f"/api/aurem-dev/oauth/authorize?{qs}", status_code=302)

    user = await db.dev_users.find_one(
        {"email": (email or "").strip().lower()},
        {"_id": 0, "user_id": 1, "email": 1, "password": 1, "is_admin": 1, "tier": 1},
    )
    bad_creds = (
        not user
        or not user.get("password")
        or not bcrypt.checkpw(password.encode(), user["password"].encode())
    )
    if bad_creds:
        return _reshow("invalid_credentials")

    # Admin-only gate — ORA Chat MCP resource ONLY. Every other
    # existing resource on this shared OAuth server (e.g. the general
    # MCP "Claude Directory" listing) stays open to any dev_user.
    if resource_path == MCP_ORA_CHAT_RESOURCE_PATH:
        if not (user.get("is_admin") or user.get("tier") == "founder"):
            return _reshow("admin_required")

    # Issue one-time, 10-min auth code
    code = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    await db.oauth_codes.insert_one({
        "code":                  code,
        "user_id":               user["user_id"],
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "scope":                 scope,
        "resource":              resource_path,
        "code_challenge":        code_challenge,
        "code_challenge_method": code_challenge_method,
        "created_at":            now,
        # 2026-08-27 — TTL fix: real BSON Date (was `time.time()`
        # float — the `oauth_codes.expires_at` TTL index never
        # expired these rows).
        "expires_at":            now + timedelta(seconds=AUTH_CODE_TTL),
        "used":                  False,
    })

    sep = "&" if "?" in redirect_uri else "?"
    callback = f"{redirect_uri}{sep}code={code}"
    if state:
        callback += f"&state={urllib.parse.quote(state)}"
    logger.info(f"[oauth] issued code for user={user['user_id']} client={client_id}")
    return RedirectResponse(url=callback, status_code=302)


@router.post("/oauth/authorize/deny")
async def oauth_deny(
    client_id: str = "",
    redirect_uri: str = "",
    resource: str = "",
    state: str = "",
):
    """User clicked Cancel — bounce back with access_denied per RFC 6749 §4.1.2.1.

    2026-09-30 — SECURITY FIX: this used to redirect to ANY redirect_uri
    with zero validation — a pure open redirect (attacker builds a link
    with their own redirect_uri, victim clicks Cancel, browser is sent
    to the attacker's URL with AUREM's own domain as the referrer/
    apparent origin). Same validation as /oauth/authorize now applies;
    an invalid combination gets a local 400, never a redirect.
    """
    if not redirect_uri:
        raise HTTPException(400, "missing redirect_uri")
    ok, err_code, _dcr_client = await _validate_redirect_uri_for_client(
        client_id, redirect_uri, _resource_path(resource),
    )
    if not ok:
        raise HTTPException(400, f"{err_code}: redirect_uri is not registered for this client_id")
    sep = "&" if "?" in redirect_uri else "?"
    err = f"{redirect_uri}{sep}error=access_denied"
    if state:
        err += f"&state={urllib.parse.quote(state)}"
    return RedirectResponse(url=err, status_code=302)


# ── Token endpoint ────────────────────────────────────────────────────

@router.post("/oauth/token")
async def oauth_token(
    grant_type: str  = Form(""),
    code: str        = Form(""),
    redirect_uri: str = Form(""),
    client_id: str   = Form(""),
    code_verifier: str = Form(""),
):
    """Exchange auth code for `sk-aurem-oauth-*` access token (PKCE verified)."""
    if grant_type != "authorization_code":
        raise HTTPException(400, "unsupported_grant_type")
    if not code or not code_verifier:
        raise HTTPException(400, "missing code or code_verifier")
    # 2026-09-30 — SECURITY FIX: both used to be optional here. Without
    # them, a stolen code (via the redirect_uri attack above, or any
    # other leak) could be redeemed by ANY client presenting ANY
    # code_verifier that happens to match the ORIGINAL challenge —
    # nothing tied the redemption back to the client_id/redirect_uri
    # the code was actually issued to. RFC 6749 §4.1.3 requires both
    # for a public client's token request; we now enforce it.
    if not client_id:
        raise HTTPException(400, "invalid_request: missing client_id")
    if not redirect_uri:
        raise HTTPException(400, "invalid_request: missing redirect_uri")

    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    code_doc = await db.oauth_codes.find_one({"code": code, "used": False})
    if not code_doc:
        raise HTTPException(400, "invalid_grant: code not found, already used, or expired")
    _exp = code_doc["expires_at"]
    if isinstance(_exp, datetime):
        _exp_dt = _exp if _exp.tzinfo else _exp.replace(tzinfo=timezone.utc)
    else:
        _exp_dt = datetime.fromtimestamp(_exp, tz=timezone.utc)
    if datetime.now(timezone.utc) > _exp_dt:
        raise HTTPException(400, "invalid_grant: code expired")
    # 2026-09-30 — SECURITY FIX: both comparisons used to be skipped
    # entirely whenever the caller simply omitted the field (`if
    # redirect_uri and ...`, no client_id check at all). Now mandatory
    # and exact — the code is bound to the exact client_id + redirect_uri
    # it was issued to at /oauth/authorize.
    if not secrets.compare_digest(client_id, code_doc.get("client_id", "")):
        raise HTTPException(400, "invalid_grant: client_id mismatch")
    if not secrets.compare_digest(redirect_uri, code_doc.get("redirect_uri", "")):
        raise HTTPException(400, "invalid_grant: redirect_uri mismatch")

    # PKCE — S256
    challenge = code_doc.get("code_challenge", "")
    method    = code_doc.get("code_challenge_method", "S256")
    if method != "S256":
        raise HTTPException(400, "unsupported_code_challenge_method")
    digest   = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if not secrets.compare_digest(computed, challenge):
        raise HTTPException(400, "invalid_grant: PKCE verification failed")

    # Burn the code so a replay can't reuse it
    await db.oauth_codes.update_one(
        {"code": code},
        {"$set": {"used": True, "used_at": time.time()}},
    )

    # Issue access token → reuse the api_keys collection so MCP server's
    # existing _resolve_user (mcp.py line 194) accepts it transparently.
    token = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    now   = datetime.now(timezone.utc)
    await db.api_keys.insert_one({
        "key":        token,
        "user_id":    code_doc["user_id"],
        "client_id":  client_id,
        "scope":      code_doc.get("scope", "mcp"),
        "created_at": now,
        # 2026-08-27 — TTL fix: real BSON Date (was `time.time()`
        # float — the `api_keys.expires_at` TTL index, scoped to
        # source=="oauth" via partialFilterExpression, never expired
        # these rows).
        "expires_at": now + timedelta(seconds=TOKEN_TTL),
        "active":     True,
        "label":      f"Claude OAuth ({client_id[:32]})",
        "source":     "oauth",
        "resource":   code_doc.get("resource", ""),
    })

    logger.info(f"[oauth] issued access token for user={code_doc['user_id']} client={client_id}")
    return JSONResponse({
        "access_token": token,
        "token_type":   "Bearer",
        "expires_in":   TOKEN_TTL,
        "scope":        code_doc.get("scope", "mcp"),
    })


# ── Userinfo ──────────────────────────────────────────────────────────

@router.get("/oauth/userinfo")
async def oauth_userinfo(authorization: Optional[str] = Header(None)):
    """RFC 7662-ish userinfo endpoint. Accepts both JWT and sk-aurem-* tokens."""
    if not authorization:
        raise HTTPException(401, "missing authorization header")

    # Tokens from /oauth/token are sk-aurem-oauth-* → look up via api_keys.
    # Fall back to current_dev (JWT path) for backward compat.
    token = authorization.replace("Bearer ", "", 1).strip()
    db    = get_db()
    if token.startswith("sk-aurem-") and db is not None:
        key_doc = await db.api_keys.find_one(
            {"key": token, "active": True}, {"_id": 0, "user_id": 1, "scope": 1},
        )
        if not key_doc:
            raise HTTPException(401, "invalid_token")
        user = await db.dev_users.find_one(
            {"user_id": key_doc["user_id"]},
            {"_id": 0, "user_id": 1, "email": 1, "name": 1},
        )
        if not user:
            raise HTTPException(401, "user not found")
        return {
            "sub":   user["user_id"],
            "email": user.get("email", ""),
            "name":  user.get("name", ""),
            "scope": key_doc.get("scope", "mcp"),
        }

    user = await current_dev(authorization)
    return {
        "sub":   user["user_id"],
        "email": user.get("email", ""),
        "name":  user.get("name", ""),
    }
