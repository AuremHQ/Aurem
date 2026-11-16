"""
routers/admin_dot.py — ORA dot signup list, admin view, 2026-11-14.

GET /admin/dot/signups — the list Teji asked for at auremcto.com/admin:
every name + email + join date from the /dot page form (Mongo
collection `dot_signups`), newest first. GET /admin/dot/signups/export
— the same rows as a CSV download (one click, no extra tool needed).

Admin-gated via require_admin_dep (cto_services.auth) at the router
level — same pattern as routers/admin_harness.py. Owner-only in the
sidebar (screenKeys: [] in lib/adminNav.js) — no screen key exists for
this list yet, so no team member can be granted it by accident.
"""
from __future__ import annotations

import csv
import io
import logging
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from cto_services.auth import require_admin_dep
from cto_services.db import require_db

logger = logging.getLogger("aurem-dev.admin_dot")

router = APIRouter(dependencies=[Depends(require_admin_dep)])

_MAX_ROWS = 500


def _row(doc: dict) -> dict:
    return {
        "name": doc.get("name") or "",
        "email": doc.get("email") or "",
        "joined_at": doc.get("joined_at"),
        "source": doc.get("source") or "dot_page",
    }


@router.get("/admin/dot/signups")
async def admin_list_dot_signups() -> dict:
    db = require_db()
    docs = await db.dot_signups.find(
        {}, {"_id": 0},
    ).sort("joined_at", -1).limit(_MAX_ROWS).to_list(_MAX_ROWS)
    rows = [_row(d) for d in docs]
    return {"signups": rows, "total": len(rows)}


@router.get("/admin/dot/signups/export")
async def admin_export_dot_signups() -> StreamingResponse:
    db = require_db()
    docs = await db.dot_signups.find(
        {}, {"_id": 0},
    ).sort("joined_at", -1).limit(_MAX_ROWS).to_list(_MAX_ROWS)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "joined_at"])
    for d in docs:
        w.writerow([d.get("name") or "", d.get("email") or "", d.get("joined_at") or ""])
    buf.seek(0)
    filename = f"ora-dot-signups-{time.strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
