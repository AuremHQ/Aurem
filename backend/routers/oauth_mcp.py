"""
routers/oauth_mcp.py — 2026-09-29 OAuth 2.1 for the ORA Chat MCP server.

Implements the parts of the flow that must live under `/api` (so
Kubernetes ingress routes them to this backend): Dynamic Client
Registration (RFC 7591), the authorization endpoint (PKCE S256 only),
the token endpoint (authorization_code + refresh_token, rotating), and
one backend-native copy of the AS metadata document at an
`/api`-prefixed fallback path. The two BARE-ROOT `.well-known/*`
discovery documents are served by frontend/vite-plugins/
oauth-mcp-metadata.mjs — see that file's docstring for why (Kubernetes
ingress only sends `/api/*` to this backend; `.well-known/*` at the
domain root is NOT `/api`-prefixed).

"Login limited to admin accounts": the browser leg of this flow
(GET /oauth/authorize -> consent) redirects to the EXISTING React SPA
login page (reuses routers/auth.py's /auth/login, including 2FA) —
no new password/session system. GET /oauth/pending and POST
/oauth/authorize/approve both require a normal admin JWT via
`require_admin`, exactly like every other admin endpoint in this app.
Bearer-key auth (services/admin_gateway_keys.py) keeps working
unchanged — routers/mcp_ora_chat.py now accepts EITHER.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

import services.oauth_mcp_store as store
from cto_services.auth import require_admin
from services.rate_limiter import check_rate_limit_async

router = APIRouter(tags=["oauth-mcp"])

_DCR_RATE_LIMIT_PER_MIN = 10


def _origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}"


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RegisterBody(BaseModel):
    client_name: Optional[str] = Field(default="MCP client")
    redirect_uris: list[str] = Field(..., min_length=1, max_length=10)
    token_endpoint_auth_method: Optional[str] = Field(default="none")
    grant_types: Optional[list[str]] = None
    response_types: Optional[list[str]] = None


@router.post("/oauth/register", status_code=201)
async def dcr_register(body: RegisterBody, request: Request) -> JSONResponse:
    if not await check_rate_limit_async(f"oauth_dcr:{_client_ip(request)}", _DCR_RATE_LIMIT_PER_MIN):
        raise HTTPException(429, "Too many registration attempts. Try again shortly.")
    try:
        out = await store.register_client(
            client_name=body.client_name,
            redirect_uris=body.redirect_uris,
            token_endpoint_auth_method=body.token_endpoint_auth_method or "none",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(out, status_code=201)


@router.get("/oauth/authorize")
async def authorize(request: Request, response_type: str, client_id: str,
                     redirect_uri: str, code_challenge: str,
                     resource: str, code_challenge_method: str = "S256",
                     scope: str = "mcp", state: Optional[str] = None):
    client = await store.get_client(client_id)
    if not client or redirect_uri not in client.get("redirect_uris", []):
        # Untrusted client/redirect_uri — do NOT redirect anywhere.
        raise HTTPException(400, "invalid_client or invalid redirect_uri")
    if response_type != "code":
        return RedirectResponse(_error_redirect(redirect_uri, "unsupported_response_type", state), status_code=302)
    if code_challenge_method != "S256" or not code_challenge:
        return RedirectResponse(_error_redirect(redirect_uri, "invalid_request", state), status_code=302)
    if not store.resource_path_matches(resource):
        return RedirectResponse(_error_redirect(redirect_uri, "invalid_target", state), status_code=302)
    consent_id = await store.create_pending(
        client_id=client_id, client_name=client.get("client_name", "MCP client"),
        redirect_uri=redirect_uri, code_challenge=code_challenge,
        resource=resource, scope=scope, state=state,
    )
    origin = _origin(request)
    return RedirectResponse(f"{origin}/oauth/authorize?consent_id={consent_id}", status_code=302)


def _error_redirect(redirect_uri: str, error: str, state: Optional[str]) -> str:
    from urllib.parse import urlencode
    sep = "&" if "?" in redirect_uri else "?"
    params = {"error": error}
    if state:
        params["state"] = state
    return redirect_uri + sep + urlencode(params)


@router.get("/oauth/pending/{consent_id}")
async def get_pending(consent_id: str, authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    pending = await store.get_pending(consent_id)
    if not pending:
        raise HTTPException(404, "This authorization request has expired. Ask Claude to reconnect.")
    return {
        "client_name": pending["client_name"],
        "scope": pending["scope"],
        "redirect_uri": pending["redirect_uri"],
    }


class ApproveBody(BaseModel):
    consent_id: str
    decision: str = Field(..., pattern=r"^(allow|deny)$")


@router.post("/oauth/authorize/approve")
async def approve(body: ApproveBody, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    pending = await store.get_pending(body.consent_id)
    if not pending:
        raise HTTPException(404, "This authorization request has expired. Ask Claude to reconnect.")
    await store.delete_pending(body.consent_id)
    if body.decision == "deny":
        return {"redirect_url": _error_redirect(pending["redirect_uri"], "access_denied", pending.get("state"))}
    code = await store.create_auth_code(pending, admin_user_id=user["user_id"])
    from urllib.parse import urlencode
    sep = "&" if "?" in pending["redirect_uri"] else "?"
    issuer = f"{_origin(request)}/api/aurem-dev/mcp"
    params = {"code": code, "iss": issuer}
    if pending.get("state"):
        params["state"] = pending["state"]
    query = urlencode(params)
    return {"redirect_url": pending["redirect_uri"] + sep + query}


@router.post("/oauth/token")
async def token(request: Request, grant_type: str = Form(...),
                 code: Optional[str] = Form(None), redirect_uri: Optional[str] = Form(None),
                 client_id: Optional[str] = Form(None), code_verifier: Optional[str] = Form(None),
                 resource: Optional[str] = Form(None), refresh_token: Optional[str] = Form(None),
                 authorization: Optional[str] = Header(None)):
    cid = client_id
    csecret = None
    if authorization and authorization.lower().startswith("basic "):
        import base64
        try:
            raw = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
            cid, csecret = raw.split(":", 1)
        except Exception:
            raise HTTPException(401, "invalid_client")
    if not cid:
        raise HTTPException(400, "client_id required")
    client = await store.verify_client_secret(cid, csecret)
    if not client:
        raise HTTPException(401, "invalid_client")

    if grant_type == "authorization_code":
        if not code or not redirect_uri or not code_verifier or not resource:
            raise HTTPException(400, "missing required parameters")
        if not store.resource_path_matches(resource):
            raise HTTPException(400, "invalid_target")
        row = await store.consume_auth_code(code, cid, redirect_uri, code_verifier)
        if not row:
            raise HTTPException(400, "invalid_grant")
        pair = await store.issue_token_pair(
            client_id=cid, admin_user_id=row["admin_user_id"],
            resource=row["resource"], scope=row["scope"],
        )
        return pair
    if grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(400, "missing refresh_token")
        pair = await store.rotate_refresh_token(refresh_token, cid)
        if not pair:
            raise HTTPException(400, "invalid_grant")
        return pair
    raise HTTPException(400, "unsupported_grant_type")


@router.get("/mcp/.well-known/oauth-authorization-server")
async def as_metadata_fallback(request: Request):
    """Backend-native fallback discovery path (OIDC-style path
    appending: {issuer}/.well-known/...) — some clients try this
    convention before/instead of RFC 8414's path-insertion form
    served at the bare domain root by the Vite plugin. Identical
    content, different path."""
    origin = _origin(request)
    return {
        "issuer": f"{origin}/api/aurem-dev/mcp",
        "authorization_endpoint": f"{origin}/api/aurem-dev/oauth/authorize",
        "token_endpoint": f"{origin}/api/aurem-dev/oauth/token",
        "registration_endpoint": f"{origin}/api/aurem-dev/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["mcp"],
        "authorization_response_iss_parameter_supported": True,
    }
