"""
services/jev_observability.py — Robot Fix List STEP 4.

Jev (services/llm/jev_client.py) silently falls back to keyword rules
on any failure, per its own design — good for the user (never breaks
chat), bad for anyone trying to tell whether Jev is actually working
at all. This module is the ONE place both real call sites
(services/semantic_mode_router.py and
services/ora_chat/intent_router.py — confirmed the only two; grep for
`jev_client` turns up no others despite the task brief's wider
candidate list) record a success/fallback outcome, so
`GET /admin/jev-health` can show real counters instead of nothing.

In-process only (module-level dict), same tier as every other
in-process counter in this codebase (no new Mongo collection for a
diagnostics-only metric) — resets on restart, which is acceptable for
"is Jev healthy RIGHT NOW" rather than a long-term audit log.
"""
from __future__ import annotations

import time
from typing import Optional

_stats = {
    "success": 0,
    "fallback": 0,
    "last_error_class": None,
    "last_model": None,
    "last_success_at": None,
    "last_fallback_at": None,
}


def record_jev_call(
    *, ok: bool, model: Optional[str] = None, error: Optional[str] = None,
) -> None:
    """Call exactly once per real Jev attempt (not per retry inside
    jev_client — that module already retries internally). `ok=True`
    means a usable label came back; `ok=False` means the caller is
    about to fall back to keyword rules."""
    now = time.time()
    if ok:
        _stats["success"] += 1
        _stats["last_success_at"] = now
        if model:
            _stats["last_model"] = model
    else:
        _stats["fallback"] += 1
        _stats["last_fallback_at"] = now
        _stats["last_error_class"] = (error or "unknown")[:200]


def get_jev_stats() -> dict:
    """Snapshot for the admin health endpoint. `fallback_rate` is
    None until at least one call has been recorded (avoids a
    misleading 0% on a cold-started process)."""
    total = _stats["success"] + _stats["fallback"]
    return {
        **_stats,
        "total_calls": total,
        "fallback_rate": (_stats["fallback"] / total) if total else None,
    }


def _reset_for_tests() -> None:
    """Test-only — module-level state needs a clean slate between
    counter assertions."""
    _stats.update({
        "success": 0, "fallback": 0, "last_error_class": None,
        "last_model": None, "last_success_at": None, "last_fallback_at": None,
    })
