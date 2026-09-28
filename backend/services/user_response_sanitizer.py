"""
services/user_response_sanitizer.py — 2026-09-28 MASTER TASK Part A1

Backend counterpart of `frontend/src/lib/providerLabel.js`. Every raw
provider/model identifier the LLM layer attaches to a chat turn
("glm-5.2", "deepseek-v3-council-c", "claude-sonnet-maxx-direct", …)
is an internal routing detail. This is the ONE place that decides
what a *regular, non-admin* user is allowed to see instead of it —
call this before a value reaches a user-facing response, never send
the raw value through directly.

Admin-only endpoints (`require_admin`-gated, e.g. routers/ora_chat.py,
routers/admin_analytics.py) are NOT in scope — they're allowed to see
the real provider/model/cost, this module is not used there.
"""
from __future__ import annotations
from typing import Optional


def sanitize_provider_for_user(raw: Optional[str]) -> str:
    """Mirrors frontend/src/lib/providerLabel.js::brandProvider — same
    contract, same fallback, so the two can never drift apart."""
    return "ORA" if raw else ""


def sanitize_provenance_for_user(raw: Optional[dict]) -> dict:
    """Same idea as `sanitize_provider_for_user`, for the richer
    `llm_provenance` shape returned by services/llm/_state.py's
    `get_last_provider()` — {"provider", "model", "is_emergency"}.
    The frontend's "⚡ free mode" pill (ChatPanel.jsx) only needs the
    boolean; `provider`/`model` are dropped, never forwarded raw."""
    if not isinstance(raw, dict):
        return {"is_emergency": False}
    return {"is_emergency": bool(raw.get("is_emergency"))}
