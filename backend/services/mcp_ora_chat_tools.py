"""
services/mcp_ora_chat_tools.py — 2026-09-28 ORA Chat MCP tools (Part A).

Shared implementation for the 3 MCP tools, used by BOTH:
  - routers/mcp_ora_chat.py (the real MCP JSON-RPC server)
  - routers/admin_gateway.py's "Test" button (calls ask_ora_chat_impl
    directly, bypassing the MCP transport, using the admin's own
    browser session instead of an MCP key)

Runs entirely through ORA Chat's EXISTING admin/founder pipeline
(services/ora_chat/session.py + services/ora_chat_v2/engine.py) — same
system prompt, same knowledge, same model routing routers/ora_chat.py
already uses. No new LLM wiring, no OpenRouter routing changes.

`conversation_id` here IS the underlying ora_chat_sessions.session_id
— no separate mapping table, one concept, two names.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from cto_services.db import get_db
from services.ora_chat import cost_tracker
from services.ora_chat import session as ora_session
from services.ora_chat_v2.engine import run_turn as _v2_run_turn

logger = logging.getLogger(__name__)

ASK_ORA_CHAT_TIMEOUT_S = 60.0


async def ask_ora_chat_impl(admin_user_id: str, message: str,
                             conversation_id: Optional[str] = None,
                             context: Optional[str] = None,
                             key_id: Optional[str] = None) -> dict:
    """Runs one turn through ORA Chat's real pipeline. Raises
    RuntimeError/ValueError on failure — callers map that to the
    appropriate transport-level error (JSON-RPC error / HTTP error)."""
    message = (message or "").strip()
    if not message:
        raise ValueError("`message` is required")

    if conversation_id:
        sess = await ora_session.get_session(conversation_id, admin_user_id)
        if not sess:
            raise ValueError(f"conversation_id not found: {conversation_id}")
    else:
        sess = await ora_session.create_session(admin_user_id, title=message[:60])
        conversation_id = sess["session_id"]

    full_message = message
    if context and context.strip():
        full_message = f"Context from Claude: {context.strip()}\n\n{message}"

    await ora_session.append_message(
        conversation_id, admin_user_id, role="user", content=full_message,
    )
    sess = await ora_session.get_session(conversation_id, admin_user_id)

    db = get_db()
    final_evt: dict = {}
    error_evt: dict = {}
    accumulated_delta = ""
    timed_out = False

    async def _drain():
        nonlocal final_evt, error_evt, accumulated_delta
        async for evt in _v2_run_turn(
                db, admin_id=admin_user_id, session=sess or {},
                user_message=full_message, think_mode=False, advise_only=False,
                page_inspection=None):
            if evt.get("type") == "final":
                final_evt = evt
            elif evt.get("type") == "error":
                error_evt = evt
            elif evt.get("type") == "delta":
                accumulated_delta += evt.get("content") or ""

    try:
        await asyncio.wait_for(_drain(), timeout=ASK_ORA_CHAT_TIMEOUT_S)
    except asyncio.TimeoutError:
        timed_out = True
    except Exception as e:  # noqa: BLE001 — mirror routers/ora_chat.py's boundary
        logger.error("ask_ora_chat_impl crashed mid-turn (conv=%s admin=%s): %r",
                     conversation_id, admin_user_id, e, exc_info=True)
        if key_id:
            from services.admin_gateway_keys import log_usage
            await log_usage(key_id, "ask_ora_chat", status="error")
        raise RuntimeError(f"ORA Chat turn failed: {type(e).__name__}: {e}") from e

    if timed_out:
        partial = accumulated_delta.strip() or "(ORA hasn't produced a response yet.)"
        if key_id:
            from services.admin_gateway_keys import log_usage
            await log_usage(key_id, "ask_ora_chat", status="timeout")
        return {
            "reply": (
                f"{partial}\n\n[ORA is still working — this reply was cut short "
                f"after {int(ASK_ORA_CHAT_TIMEOUT_S)}s. Call ask_ora_chat again "
                f"with the same conversation_id to continue.]"
            ),
            "conversation_id": conversation_id,
            "model_used": None,
        }

    if error_evt and not final_evt:
        if key_id:
            from services.admin_gateway_keys import log_usage
            await log_usage(key_id, "ask_ora_chat", status="error")
        raise RuntimeError(error_evt.get("error") or "ORA Chat returned an error")

    model = final_evt.get("model") or ""
    tokens_in = int(final_evt.get("tokens_in") or 0)
    tokens_out = int(final_evt.get("tokens_out") or 0)
    await ora_session.append_message(
        conversation_id, admin_user_id, role="assistant",
        content=final_evt.get("content", ""), model=model,
        input_tokens=tokens_in, output_tokens=tokens_out,
        message_id=uuid.uuid4().hex,
    )
    if key_id:
        from services.admin_gateway_keys import log_usage
        cost = cost_tracker.compute_cost_usd(model, tokens_in, tokens_out)
        await log_usage(key_id, f"ask_ora_chat:{model}", tokens_in, tokens_out, cost, "ok")

    return {
        "reply": final_evt.get("content", ""),
        "conversation_id": conversation_id,
        "model_used": model,
    }


async def list_ora_conversations_impl(admin_user_id: str, limit: int = 20) -> dict:
    limit = max(1, min(50, int(limit or 20)))
    rows = await ora_session.list_sessions(admin_user_id, limit=limit)
    return {
        "conversations": [
            {
                "conversation_id": r.get("session_id"),
                "title": r.get("title"),
                "updated_at": r.get("updated_at"),
                "message_count": r.get("message_count", 0),
            }
            for r in rows
        ],
        "count": len(rows),
    }


async def get_ora_conversation_impl(admin_user_id: str, conversation_id: str) -> dict:
    conversation_id = (conversation_id or "").strip()
    if not conversation_id:
        raise ValueError("`conversation_id` is required")
    sess = await ora_session.get_session(conversation_id, admin_user_id)
    if not sess:
        raise ValueError(f"conversation_id not found: {conversation_id}")
    messages = (sess.get("messages") or [])[-200:]
    return {
        "conversation_id": conversation_id,
        "title": sess.get("title"),
        "messages": [
            {"role": m.get("role"), "content": m.get("content"), "ts": m.get("ts")}
            for m in messages
        ],
        "count": len(messages),
    }
