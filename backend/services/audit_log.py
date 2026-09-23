"""
audit_log.py — Core 5 of the verification foundation.

Writes one row per ORA turn to MongoDB collection `ora_audit`. This is
the ground truth when something goes wrong — admins (and any future
diagnostic UI) read this instead of guessing from screenshots.

Schema (one document per turn)::

    {
      "_id":                            str (uuid),
      "turn_id":                        str (uuid, same as _id),
      "project_id":                     str | None,
      "user_id":                        str,
      "timestamp":                      datetime (UTC ISO),
      "tools_called":                   [str, ...]     # "read_repo_file:backend/server.py"
      "citation_guard_triggered":       bool,
      "citation_guard_paths_fetched":   [str, ...],
      "citation_guard_unverified":      [str, ...],
      "system_signals_emitted":         [str, ...],    # "github_auth_failed"
      "llm_model":                      str,           # "deepseek" | "claude" | …
      "response_tokens":                int,
      "was_retry":                      bool,
    }

Iter 209 — Aurem core architecture.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)

COLLECTION = "ora_audit"


async def _fire_signal_alerts(
    db, *, signals: list[str], user_id: str, project_id: str | None,
    turn_id: str, tools_called: list[str],
) -> None:
    """2026-09-07 — 'Signal Alerts': ping the founder the moment a
    real system signal (github_auth_failed, repo_not_found, etc.)
    fires, instead of them having to check the Audit feed's
    'Signals only' toggle manually. Reuses the existing
    founder_alerts Resend channel (dedup: max 1 email per signal type
    per 6h, same as every other guard-critical alert). Scheduled via
    create_task from record_turn — never awaited inline, so a slow
    or failing Resend call can't add latency to the chat turn whose
    audit write triggered it."""
    try:
        from services.founder_alerts import send_founder_alert
        for sig in sorted(set(signals)):
            await send_founder_alert(
                db,
                source_key=f"ora_signal:{sig}",
                level="warning",
                guard="ora_signal",
                title=f"ORA signal fired: {sig}",
                detail=(
                    f"user_id={user_id}\n"
                    f"project_id={project_id or '—'}\n"
                    f"turn_id={turn_id}\n"
                    f"tools_called={tools_called}"
                ),
            )
    except Exception as e:                                   # noqa: BLE001
        logger.debug("audit_log: signal alert failed (non-blocking): %r", e)


async def record_turn(
    *,
    user_id:                          str,
    project_id:                       str | None,
    tools_called:                     Iterable[str],
    citation_guard_triggered:         bool,
    citation_guard_paths_fetched:     Iterable[str],
    citation_guard_unverified:        Iterable[str],
    system_signals_emitted:           Iterable[str],
    llm_model:                        str,
    response_tokens:                  int,
    was_retry:                        bool,
    extra:                            dict[str, Any] | None = None,
) -> str | None:
    """Persist one audit row. Returns the turn_id, or None if the DB
    is unavailable (audit is best-effort — should never block ORA)."""
    try:
        from cto_services.db import get_db
        db = get_db()
    except Exception as e:                                   # noqa: BLE001
        logger.warning("audit_log: get_db failed: %r", e)
        return None
    if db is None:
        return None

    turn_id = str(uuid.uuid4())
    doc: dict[str, Any] = {
        "_id":                            turn_id,
        "turn_id":                        turn_id,
        "project_id":                     project_id,
        "user_id":                        user_id,
        "timestamp":                      datetime.now(timezone.utc).isoformat(),
        "tools_called":                   list(tools_called),
        "citation_guard_triggered":       bool(citation_guard_triggered),
        "citation_guard_paths_fetched":   list(citation_guard_paths_fetched),
        "citation_guard_unverified":      list(citation_guard_unverified),
        "system_signals_emitted":         list(system_signals_emitted),
        "llm_model":                      llm_model or "",
        "response_tokens":                int(response_tokens or 0),
        "was_retry":                      bool(was_retry),
    }
    if extra:
        doc["extra"] = extra

    try:
        await db[COLLECTION].insert_one(doc)
    except Exception as e:                                   # noqa: BLE001
        logger.warning("audit_log.insert_one failed: %r", e)
        return None

    signals = list(system_signals_emitted)
    if signals:
        asyncio.create_task(_fire_signal_alerts(
            db, signals=signals, user_id=user_id, project_id=project_id,
            turn_id=turn_id, tools_called=list(tools_called),
        ))
    return turn_id


async def list_turns(
    *,
    user_id:      str | None = None,
    project_id:   str | None = None,
    limit:        int = 100,
    signals_only: bool = False,
) -> list[dict]:
    """Read recent audit rows for the admin/diagnostic UI.

    signals_only: system signals (github_auth_failed, repo_not_found,
    etc.) are genuinely rare — 3 out of 3,370 turns as of 2026-09-07 —
    so the default newest-first page almost never shows one even
    though the field itself works correctly. This lets the admin UI
    jump straight to the rows that ever had one instead of scrolling
    past thousands of empty ones.
    """
    try:
        from cto_services.db import get_db
        db = get_db()
    except Exception:                                        # noqa: BLE001
        return []
    if db is None:
        return []
    q: dict[str, Any] = {}
    if user_id:
        q["user_id"] = user_id
    if project_id:
        q["project_id"] = project_id
    if signals_only:
        q["system_signals_emitted"] = {"$exists": True, "$ne": []}
    try:
        return await db[COLLECTION].find(q, {"_id": 0}).sort("timestamp", -1).to_list(limit)
    except Exception as e:                                   # noqa: BLE001
        logger.warning("audit_log.list_turns failed: %r", e)
        return []
