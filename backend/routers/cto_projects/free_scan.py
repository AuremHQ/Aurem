"""
routers/cto_projects/free_scan.py — Master Prompt v2 PART 4.1.

GET /cto/projects/{project_id}/free-scan — the free-scan RESULTS
surface. Does not trigger anything (the scan already runs
automatically on connect, see services/project_onboarding_scan.py);
this only reads whatever services/free_scan_results.py has persisted.

Guards:
  - Ownership: services.free_scan_results.get_free_scan_results
    itself returns None for a project the caller doesn't own (same
    (project_id, user_id) check every sibling endpoint in
    management.py uses) — surfaced here as a 404, never leaking
    whether the project_id exists at all.
  - Rate limit: per-account AND per-IP, via the same
    services.rate_limiter.check_rate_limit_async primitive used
    throughout this codebase (routers/auth.py's login guard,
    routers/admin_pricing.py's PRE-STEP (d) mutation guard).
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException, Request

from cto_services.auth import current_dev
from cto_services.db import require_db

from . import router

_FREE_SCAN_READ_PER_MIN = int(os.getenv("FREE_SCAN_READ_RATE_PER_MIN", "30"))
_FREE_SCAN_READ_PER_IP_PER_MIN = int(os.getenv("FREE_SCAN_READ_RATE_PER_IP_PER_MIN", "60"))


@router.get("/projects/{project_id}/free-scan")
async def get_free_scan(
    project_id: str, authorization: str | None = Header(None),
    request: Request = None,
) -> dict:
    user = await current_dev(authorization)
    user_id = user["user_id"]

    from services.rate_limiter import check_rate_limit_async, client_ip_from_request
    if not await check_rate_limit_async(f"free_scan_read:{user_id}", _FREE_SCAN_READ_PER_MIN):
        raise HTTPException(429, "Too many free-scan result requests for this account.")
    if request is not None:
        ip = client_ip_from_request(request)
        if not await check_rate_limit_async(f"free_scan_read_ip:{ip}", _FREE_SCAN_READ_PER_IP_PER_MIN):
            raise HTTPException(429, "Too many free-scan result requests from this IP.")

    db = require_db()
    from services.free_scan_results import get_free_scan_results
    result = await get_free_scan_results(db, user_id=user_id, project_id=project_id)
    if result is None:
        raise HTTPException(404, "free scan not found")
    return result
