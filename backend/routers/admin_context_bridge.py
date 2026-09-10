"""
routers/admin_context_bridge.py — AUREM Admin Context Bridge (2026-09-09).

Receives a sanitized snapshot (visible text, console errors, network
timing) captured by the companion Chrome extension from the founder's
active tab (e.g. an external vendor portal) and drops it into an
EXISTING ORA chat session so ORA can reference it in that
conversation — no new LLM-context-assembly code needed, this just
reuses `chat_helpers._persist_turn`, the exact same function every
other chat turn is written through (`routers/chat/turn.py`, `stream.py`,
`watchdog.py`).

Security:
  - Admin/founder only (`require_admin_dep`, same gate every other
    admin router uses).
  - The extension does its own sanitization before dispatch (per its
    own `src/sanitize.ts`) — this is a SECOND, independent pass,
    defense-in-depth, in case a future extension build/bug skips it.
    Never trust the client alone for a payload this sensitive.
  - Hard size caps on every field so one huge/malicious payload can't
    blow up the `chat_sessions` document (already capped at 200 turns
    total, see `_persist_turn`) or LLM context-window cost downstream.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import current_dev, require_admin_dep
from cto_services.db import get_db
from services.chat_helpers import _persist_turn

router = APIRouter(
    prefix="/admin/ora",
    tags=["Admin-Context-Bridge"],
    dependencies=[Depends(require_admin_dep)],
)

_MAX_TEXT_CHARS = 4000
_MAX_ERROR_ITEMS = 20
_MAX_ERROR_CHARS = 600
_MAX_NETWORK_ITEMS = 15

# Server-side sanitize pass (2nd layer — the extension already strips
# these client-side). Order matters: run the most specific patterns
# (explicit key=value pairs) before the generic long-opaque-token
# catch-all, so we redact with a useful label where we can identify
# what kind of secret it was.
_SANITIZE_PATTERNS = [
    (re.compile(r'(?i)\b(password|passwd|pwd)\s*[=:]\s*\S+'), "[REDACTED_PASSWORD]"),
    (re.compile(r'(?i)\bauthorization\s*:\s*bearer\s+\S+'), "[REDACTED_AUTH_HEADER]"),
    (re.compile(r'(?i)\b(api[_-]?key|secret|token|access[_-]?key)\s*[=:]\s*\S+'), "[REDACTED_SECRET]"),
    (re.compile(r'\bsk-[A-Za-z0-9_-]{16,}\b'), "[REDACTED_KEY]"),
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b'), "[REDACTED_GH_TOKEN]"),
    (re.compile(r'\bAKIA[0-9A-Z]{12,}\b'), "[REDACTED_AWS_KEY]"),
    # Generic JWT-shaped token (three dot-separated base64url segments).
    (re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'), "[REDACTED_JWT]"),
]


def _sanitize(text: str) -> str:
    if not text:
        return ""
    out = text
    for pattern, replacement in _SANITIZE_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


class ContextBridgePayload(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    url: str = Field("", max_length=2000)
    title: str = Field("", max_length=300)
    visible_text: str = Field("", max_length=200_000)
    console_errors: list[str] = Field(default_factory=list, max_length=500)
    network_metrics: list[dict] = Field(default_factory=list, max_length=200)
    captured_at: Optional[str] = None


def _format_snapshot_message(payload: ContextBridgePayload, clean_text: str,
                              clean_errors: list[str]) -> str:
    lines = [
        "\U0001F4CE **Browser context received** (via AUREM Context Bridge extension)",
        "",
        f"**Source:** {payload.url or 'unknown'}",
        f"**Page title:** {payload.title or '(none)'}",
        f"**Captured at:** {payload.captured_at or 'unknown'}",
        "",
    ]
    if clean_text:
        lines += ["**Visible page text (sanitized, truncated):**", "```", clean_text, "```", ""]
    if clean_errors:
        lines += ["**Console errors captured:**"]
        lines += [f"- {e}" for e in clean_errors]
        lines += [""]
    if payload.network_metrics:
        lines += ["**Slowest network requests during capture:**"]
        top = sorted(payload.network_metrics, key=lambda m: m.get("duration") or 0, reverse=True)[:_MAX_NETWORK_ITEMS]
        for m in top:
            name = str(m.get("name", ""))[:200]
            duration = m.get("duration")
            size = m.get("transferSize")
            lines.append(f"- `{name}` — {round(duration, 1) if isinstance(duration, (int, float)) else '?'}ms"
                         + (f", {size}B" if isinstance(size, (int, float)) else ""))
    return "\n".join(lines).strip()


@router.post("/context-bridge")
async def receive_context_bridge(
    payload: ContextBridgePayload,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")

    clean_text = _sanitize(payload.visible_text)[:_MAX_TEXT_CHARS]
    clean_errors = [
        _sanitize(e)[:_MAX_ERROR_CHARS] for e in (payload.console_errors or [])[:_MAX_ERROR_ITEMS]
    ]
    clean_url = _sanitize(payload.url)[:2000]

    # Audit trail — independent of the chat-session turn below, so the
    # capture is still traceable even if the founder later clears that
    # chat's history.
    await db.ora_context_bridge_snapshots.insert_one({
        "user_id": user["user_id"],
        "session_id": payload.session_id,
        "url": clean_url,
        "title": payload.title[:300],
        "text_chars": len(clean_text),
        "error_count": len(clean_errors),
        "network_entry_count": len(payload.network_metrics or []),
        "captured_at": payload.captured_at,
        "received_at": time.time(),
    })

    message = _format_snapshot_message(payload, clean_text, clean_errors)
    await _persist_turn(
        user_id=user["user_id"],
        session_id=payload.session_id,
        user_prompt=f"[Context Bridge] Sent browser context from {clean_url or 'a tab'}",
        assistant_reply=message,
        provider="context_bridge",
    )

    return {"ok": True, "preview": message[:200]}
