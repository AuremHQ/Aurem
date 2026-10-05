"""
routers/admin_gateway.py — 2026-09-28 ORA Chat MCP/Gateway key admin API.

Admin panel management for the two new admin-only access paths:
  scope="mcp"     → ask_ora_chat MCP server (routers/mcp_ora_chat.py)
  scope="gateway" → Claude Desktop Anthropic-compatible gateway (Part B)

Every route here is gated with `require_admin` server-side — this is
NOT a UI-only restriction. The raw key value is returned exactly once,
at creation/regeneration time, and never again.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from cto_services.auth import require_admin
import services.admin_gateway_keys as gw_keys
from routers.oauth import list_ora_chat_oauth_clients, revoke_ora_chat_oauth_client

router = APIRouter(prefix="/admin/gateway", tags=["Admin — ORA Chat MCP/Gateway"])


class CreateKeyBody(BaseModel):
    scope: str = Field(..., pattern=r"^(mcp|gateway)$")
    label: str = Field(..., min_length=1, max_length=80)
    rate_limit_per_min: Optional[int] = Field(default=None, ge=1, le=6000)
    spend_cap_monthly_usd: Optional[float] = Field(default=None, ge=0, le=100000)


class UpdateLimitsBody(BaseModel):
    rate_limit_per_min: Optional[int] = Field(default=None, ge=1, le=6000)
    spend_cap_monthly_usd: Optional[float] = Field(default=None, ge=0, le=100000)


@router.post("/keys")
async def create_key(body: CreateKeyBody, authorization: Optional[str] = Header(None), request: Request = None):
    user = await require_admin(authorization, request=request)
    kwargs = {}
    if body.rate_limit_per_min is not None:
        kwargs["rate_limit_per_min"] = body.rate_limit_per_min
    if body.spend_cap_monthly_usd is not None:
        kwargs["spend_cap_monthly_usd"] = body.spend_cap_monthly_usd
    raw, doc = await gw_keys.generate_key(
        scope=body.scope, label=body.label, created_by_user_id=user["user_id"], **kwargs,
    )
    doc.pop("key_hash", None)
    return {"ok": True, "key": raw, "key_doc": doc}


@router.get("/keys")
async def get_keys(scope: str, authorization: Optional[str] = Header(None), request: Request = None):
    await require_admin(authorization, request=request)
    if scope not in ("mcp", "gateway"):
        raise HTTPException(400, "scope must be 'mcp' or 'gateway'")
    return {"ok": True, "keys": await gw_keys.list_keys(scope)}


@router.post("/keys/{key_id}/revoke")
async def revoke(key_id: str, scope: str, authorization: Optional[str] = Header(None), request: Request = None):
    await require_admin(authorization, request=request)
    ok = await gw_keys.revoke_key(key_id, scope)
    if not ok:
        raise HTTPException(404, "Key not found")
    return {"ok": True}


@router.post("/keys/{key_id}/regenerate")
async def regenerate(key_id: str, scope: str, label: Optional[str] = None,
                      authorization: Optional[str] = Header(None), request: Request = None):
    user = await require_admin(authorization, request=request)
    existing = [k for k in await gw_keys.list_keys(scope) if k["key_id"] == key_id]
    if not existing:
        raise HTTPException(404, "Key not found")
    old = existing[0]
    await gw_keys.revoke_key(key_id, scope)
    raw, doc = await gw_keys.generate_key(
        scope=scope, label=label or old.get("label") or "Unnamed key",
        created_by_user_id=user["user_id"],
        rate_limit_per_min=old.get("rate_limit_per_min", gw_keys.DEFAULT_RATE_LIMIT_PER_MIN),
        spend_cap_monthly_usd=old.get("spend_cap_monthly_usd", gw_keys.DEFAULT_SPEND_CAP_USD),
    )
    doc.pop("key_hash", None)
    return {"ok": True, "key": raw, "key_doc": doc}


@router.patch("/keys/{key_id}/limits")
async def patch_limits(key_id: str, scope: str, body: UpdateLimitsBody,
                        authorization: Optional[str] = Header(None), request: Request = None):
    await require_admin(authorization, request=request)
    ok = await gw_keys.update_limits(
        key_id, scope,
        rate_limit_per_min=body.rate_limit_per_min,
        spend_cap_monthly_usd=body.spend_cap_monthly_usd,
    )
    if not ok:
        raise HTTPException(404, "Key not found or nothing to update")
    return {"ok": True}


@router.post("/test/mcp")
async def test_mcp(authorization: Optional[str] = Header(None), request: Request = None):
    """Admin-panel "Test" button — exercises ask_ora_chat("ping")
    through the exact same internal path the MCP tool uses, without
    needing a real MCP key (this call is gated by the browser's own
    admin session instead)."""
    user = await require_admin(authorization, request=request)
    from services.mcp_ora_chat_tools import ask_ora_chat_impl
    try:
        result = await ask_ora_chat_impl(
            admin_user_id=user["user_id"], message="ping",
            conversation_id=None, context=None, key_id=None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, f"ORA Chat call failed: {e}")
    return {"ok": True, "result": result}


@router.get("/oauth-clients")
async def get_oauth_clients(authorization: Optional[str] = Header(None), request: Request = None):
    """2026-09-30 — DCR-registered OAuth 2.1 clients (shared server,
    routers/oauth.py) that hold an active token for the ORA Chat MCP
    resource specifically. Read-only visibility into which Claude
    connectors etc. are actually connected."""
    await require_admin(authorization, request=request)
    return {"ok": True, "clients": await list_ora_chat_oauth_clients()}


@router.post("/oauth-clients/{client_id}/revoke")
async def revoke_oauth_client(client_id: str, authorization: Optional[str] = Header(None), request: Request = None):
    """Deactivates all ORA-Chat-MCP-scoped tokens for this OAuth
    client and deletes its DCR registration — forces a fresh
    /oauth/authorize + admin consent next time that connector tries
    to reconnect."""
    await require_admin(authorization, request=request)
    ok = await revoke_ora_chat_oauth_client(client_id)
    if not ok:
        raise HTTPException(404, "Client not found")
    return {"ok": True}
