"""
routers/mcp_ora_chat.py — 2026-09-28 ORA Chat MCP server (Part A).

ADMIN-ONLY remote MCP server exposing ORA Chat (AUREM's internal
admin/founder assistant, routers/ora_chat.py's pipeline) as 3 tools
Claude Desktop can call inside any conversation.

Spec: JSON-RPC 2.0 over a single HTTP endpoint (Streamable HTTP
transport style), mirroring the exact, already-proven pattern used by
routers/mcp.py (that server is confirmed working with real Claude
Desktop / Cursor clients today). Official `mcp` PyPI SDK was evaluated
and NOT used here — SDK 1.10.0 is the newest version compatible with
this app's pinned `pydantic==2.10.0`, but its `mcp.types` module still
fails to import against that pydantic version (a real, confirmed
incompatibility, not a style choice). Upgrading pydantic app-wide to
chase the official SDK was judged too risky for a single new admin
feature, so this router hand-rolls the same wire protocol
`routers/mcp.py` already ships in production.

Auth: `Authorization: Bearer <admin key>` (minted via POST
/admin/gateway/keys with scope="mcp", backend/services/
admin_gateway_keys.py) OR `Authorization: Bearer <OAuth access token>`
issued by the OAuth 2.1 flow in routers/oauth_mcp.py (Dynamic Client
Registration + PKCE — see /.well-known/oauth-authorization-server and
/.well-known/oauth-protected-resource, served by
frontend/vite-plugins/oauth-mcp-metadata.mjs). EITHER path is checked
on every call; both re-verify the token/key's owning user is STILL an
admin right now, is not revoked, is under its per-key rate limit, and
(for ask_ora_chat) is under its monthly spend cap.

GET  /mcp/ora-chat  -> manifest (server info + tool list)
POST /mcp/ora-chat  -> JSON-RPC 2.0: initialize / tools/list / tools/call
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

import services.admin_gateway_keys as gw_keys
import services.oauth_mcp_store as oauth_store
from services.mcp_ora_chat_tools import (
    ask_ora_chat_impl, get_ora_conversation_impl, list_ora_conversations_impl,
)
from services.rate_limiter import check_rate_limit_async

logger = logging.getLogger("aurem.mcp_ora_chat")
router = APIRouter(prefix="/mcp/ora-chat", tags=["mcp-ora-chat-admin"])

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME = "ORA Chat (Admin)"
MCP_SERVER_VERSION = "1.0.0"

_RPC_PARSE_ERROR = -32700
_RPC_INVALID_REQUEST = -32600
_RPC_METHOD_NOT_FOUND = -32601
_RPC_INVALID_PARAMS = -32602
_RPC_UNAUTHORIZED = -32001
_RPC_RATE_LIMITED = -32003
_RPC_SPEND_CAPPED = -32004
_RPC_TOOL_FAILED = -32002

TOOLS: list[dict[str, Any]] = [
    {
        "name": "ask_ora_chat",
        "description": (
            "Ask ORA Chat (AUREM's internal admin/founder assistant) a "
            "question and get its answer. Use whenever the founder wants "
            "ORA's own knowledge, opinion, or help with AUREM internals "
            "instead of Claude's own answer. Pass `conversation_id` from "
            "a previous call to continue the SAME ORA Chat thread with "
            "memory of earlier turns; omit it to start a new thread. "
            "Returns ORA's reply, the conversation_id (save it to "
            "continue later), and which model ORA used."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The question or instruction for ORA Chat.",
                    "minLength": 1,
                    "maxLength": 20000,
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Continue an existing ORA Chat thread by its id.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional extra background text to give ORA.",
                    "maxLength": 8000,
                },
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Ask ORA Chat", "readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "list_ora_conversations",
        "description": (
            "List recent ORA Chat threads (id, title, last-updated time). "
            "Use to find a conversation_id to resume with ask_ora_chat or "
            "get_ora_conversation. Returns up to `limit` threads, newest first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "additionalProperties": False,
        },
        "annotations": {"title": "List ORA Conversations", "readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "get_ora_conversation",
        "description": (
            "Read the full message history of one ORA Chat thread by its "
            "conversation_id. Use to review past context before continuing "
            "a thread with ask_ora_chat. Returns role/content/timestamp for "
            "each message (most recent 200)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "minLength": 1},
            },
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Get ORA Conversation", "readOnlyHint": True, "destructiveHint": False},
    },
]

_TOOL_NAMES = {t["name"] for t in TOOLS}


def _rpc_ok(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _server_manifest() -> dict:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        "capabilities": {"tools": {"listChanged": False}},
        "instructions": (
            "ORA Chat (Admin) — ask AUREM's internal assistant questions, "
            "and browse/continue past threads. Admin-only."
        ),
    }


@router.get("")
async def manifest() -> JSONResponse:
    payload = _server_manifest()
    payload["tools"] = TOOLS
    payload["transport"] = "streamable-http"
    payload["auth"] = {
        "type": "oauth2.1+bearer",
        "note": "Two ways in: (1) Authorization: Bearer <admin MCP key> — "
                "generate one in the admin panel (API Keys page), or "
                "(2) OAuth 2.1 with Dynamic Client Registration + PKCE — "
                "Claude Desktop custom connectors discover this "
                "automatically via /.well-known/oauth-protected-resource. "
                "Both are admin-only.",
    }
    return JSONResponse(payload)


def _origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}"


def _www_authenticate(request: Request) -> str:
    prm_url = f"{_origin(request)}/.well-known/oauth-protected-resource/api/aurem-dev/mcp/ora-chat"
    return f'Bearer realm="mcp", resource_metadata="{prm_url}"'


async def _authenticate(authorization: Optional[str]) -> tuple[Optional[dict], Optional[dict], int]:
    """Returns (key_doc, rpc_error_dict_or_None, http_status). Accepts
    EITHER a static admin MCP key (services/admin_gateway_keys.py) OR
    an OAuth 2.1 access token (services/oauth_mcp_store.py) — both are
    normalized to the same key_doc shape so rate-limit/spend-cap/
    usage-log code below doesn't need to know which path was used."""
    if not authorization:
        return None, {"code": _RPC_UNAUTHORIZED, "message": "Missing Authorization header"}, 401
    raw = authorization.replace("Bearer ", "", 1).strip()
    key_doc = await gw_keys.verify_key(raw, scope="mcp")
    if not key_doc:
        key_doc = await oauth_store.verify_access_token(raw)
    if not key_doc:
        return None, {"code": _RPC_UNAUTHORIZED, "message": "Invalid, revoked, expired, or non-admin credential"}, 403
    limit = key_doc.get("rate_limit_per_min", gw_keys.DEFAULT_RATE_LIMIT_PER_MIN)
    if not await check_rate_limit_async(f"mcp_ora_chat:{key_doc['key_id']}", limit):
        return None, {"code": _RPC_RATE_LIMITED, "message": f"Rate limit exceeded ({limit}/min). Slow down."}, 429
    return key_doc, None, 200


@router.get("/.well-known/oauth-protected-resource")
async def prm_fallback(request: Request):
    """Backend-native fallback discovery path (path-appending
    convention: {resource}/.well-known/...). The primary, RFC
    8414/9728-correct copy is served at the bare domain root by
    frontend/vite-plugins/oauth-mcp-metadata.mjs and is what the
    `resource_metadata` hint below points to."""
    origin = _origin(request)
    return {
        "resource": f"{origin}/api/aurem-dev/mcp/ora-chat",
        "authorization_servers": [f"{origin}/api/aurem-dev/mcp"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
        "resource_name": "ORA Chat MCP (admin only)",
    }


@router.post("")
async def mcp_rpc(request: Request, authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_err(None, _RPC_PARSE_ERROR, "Invalid JSON"), status_code=400)
    if not isinstance(body, dict):
        return JSONResponse(_rpc_err(None, _RPC_INVALID_REQUEST, "Body must be a JSON object"), status_code=400)
    result, status = await _handle_one(body, authorization)
    headers = {"WWW-Authenticate": _www_authenticate(request)} if status in (401, 403) else None
    return JSONResponse(result, status_code=status, headers=headers)


async def _handle_one(req: dict, authorization: Optional[str]) -> tuple[dict, int]:
    req_id = req.get("id")
    if req.get("jsonrpc") != "2.0":
        return _rpc_err(req_id, _RPC_INVALID_REQUEST, "jsonrpc must be '2.0'"), 400
    method = req.get("method")
    params = req.get("params") or {}
    if not isinstance(method, str):
        return _rpc_err(req_id, _RPC_INVALID_REQUEST, "Missing method"), 400

    if method == "initialize":
        return _rpc_ok(req_id, _server_manifest()), 200

    key_doc, err, status = await _authenticate(authorization)
    if err:
        return _rpc_err(req_id, err["code"], err["message"]), status

    if method == "tools/list":
        return _rpc_ok(req_id, {"tools": TOOLS}), 200

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or name not in _TOOL_NAMES:
            return _rpc_err(req_id, _RPC_METHOD_NOT_FOUND, f"Unknown tool: {name!r}"), 200
        if not isinstance(args, dict):
            return _rpc_err(req_id, _RPC_INVALID_PARAMS, "`arguments` must be an object"), 200
        admin_user_id = key_doc["created_by_user_id"]
        try:
            t0 = time.monotonic()
            if name == "ask_ora_chat":
                allowed, spent = await gw_keys.check_spend_cap(key_doc)
                if not allowed:
                    return (_rpc_err(
                        req_id, _RPC_SPEND_CAPPED,
                        f"Monthly spend cap reached (${spent:.2f} spent this month). "
                        f"Raise the cap in the admin panel to continue.",
                    ), 429)
                result = await ask_ora_chat_impl(
                    admin_user_id=admin_user_id, message=args.get("message", ""),
                    conversation_id=args.get("conversation_id"), context=args.get("context"),
                    key_id=key_doc["key_id"],
                )
            elif name == "list_ora_conversations":
                result = await list_ora_conversations_impl(
                    admin_user_id=admin_user_id, limit=args.get("limit", 20),
                )
            else:  # get_ora_conversation
                result = await get_ora_conversation_impl(
                    admin_user_id=admin_user_id, conversation_id=args.get("conversation_id", ""),
                )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return (_rpc_ok(req_id, {
                "content": [{"type": "text", "text": _json_safe_dumps(result)}],
                "isError": False, "data": result, "elapsedMs": elapsed_ms,
            }), 200)
        except ValueError as e:
            return _rpc_err(req_id, _RPC_INVALID_PARAMS, str(e)), 200
        except Exception as e:
            logger.exception("[mcp-ora-chat] tool %r failed: %r", name, e)
            return _rpc_err(req_id, _RPC_TOOL_FAILED, f"Tool execution failed: {type(e).__name__}: {e}"), 200

    return _rpc_err(req_id, _RPC_METHOD_NOT_FOUND, f"Unknown method: {method!r}"), 200


def _json_safe_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str, ensure_ascii=False)

