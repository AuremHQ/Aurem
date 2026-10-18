"""
services/name_integrity_audit.py — F1 defect 1d (2026-02)

DRY-RUN ONLY. Lists existing `dev_users` rows whose stored `name`
differs from `sanitize_display_name(name)` — i.e. a name that would
have been sanitized if it signed up today, flagging rows written
before Q2 (2026-02) or that otherwise bypassed the write-time guard.

ZERO writes to any user document. The caller (routers/admin_users.py)
logs ONE audit-log row recording "the report was viewed" via the
existing services/admin_audit_log.py — the only write this flow ever
performs, and it writes to the audit log, never to a user record.

Can be pointed at ANY database this backend is configured to read —
deliberately includes `ran_against` in its own return value so the
caller never has to guess which environment a given run covered (a
preview-DB run says nothing about production unless this is actually
deployed and run against production's own MONGO_URL/DB_NAME).
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

from services.signup_guards import sanitize_display_name

# Safety cap — this is an admin diagnostic, not a hot-path query.
MAX_SCAN = 2000
# How much of the raw (possibly malicious) stored name to echo back in
# the JSON report. This is a JSON API response, not an HTML template —
# no escaping needed for the wire format itself — but we still cap
# length defensively since a few of these may be fuzz-test payloads.
RAW_NAME_PREVIEW_LEN = 200


def _ran_against() -> dict[str, str]:
    """Which DB this process is actually connected to — so a report
    run here can never be mistaken for a production result unless it
    genuinely was one."""
    db_name = os.environ.get("DB_NAME", "")
    raw_url = os.environ.get("MONGO_URL", "")
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname or raw_url
    except Exception:                                      # noqa: BLE001
        host = "unknown"
    return {"db_name": db_name, "mongo_host": host}


async def run_dry_run_audit(db) -> dict[str, Any]:
    """Read-only. Returns which DB it ran against, counts, and the
    list of mismatched rows with their proposed sanitized name.
    Applies NOTHING to any dev_users document."""
    candidates = await db.dev_users.find(
        {"name": {"$exists": True, "$nin": [None, ""]}},
        {"user_id": 1, "email": 1, "name": 1},
    ).limit(MAX_SCAN).to_list(MAX_SCAN)

    mismatched: list[dict[str, Any]] = []
    for u in candidates:
        raw_name = u.get("name")
        if not isinstance(raw_name, str):
            continue
        proposed = sanitize_display_name(raw_name) or None
        if proposed == raw_name.strip():
            continue
        mismatched.append({
            "user_id": u.get("user_id"),
            "email": u.get("email"),
            "stored_name_raw": raw_name[:RAW_NAME_PREVIEW_LEN],
            "proposed_sanitized_name": proposed,
        })

    return {
        "dry_run": True,
        "applied_any_change": False,
        "ran_against": _ran_against(),
        "scanned_count": len(candidates),
        "mismatched_count": len(mismatched),
        "users": mismatched,
    }
