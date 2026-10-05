"""_admin_common.py — shared helpers used by every admin sub-router.

Admin Team S1 (AT1a) — the decision rule itself (is_admin / tier=="founder"
/ stale-JWT DB fallback) is centralized via `is_admin_or_founder` and
`require_admin`'s own fallback is mirrored here deliberately, rather than
calling `cto_services.auth.require_admin` directly end-to-end: ~40 existing
tests patch `routers._admin_common.current_dev` to fake authentication for
every router that imports `_require_admin` from here (e.g.
test_phase2c_admin_analytics_router.py, test_phase2_admin_users_coverage.py).
Keeping the local `current_dev` call preserves that seam with zero test
edits, while `is_admin_or_founder` keeps the actual RULE in one place (the
same predicate `require_admin`'s fast path and `admin_vanguard.py` use).
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from cto_services.auth import current_dev, is_admin_or_founder
from cto_services.db import get_db


async def _require_admin(authorization: Optional[str], request=None) -> dict:
    user = await current_dev(authorization)
    if is_admin_or_founder(user):
        return user
    # Stale-JWT escape hatch: the JWT might be from before the user was
    # promoted (e.g. founder allow-list added after their last login).
    db = get_db()
    if db is not None:
        row = await db.dev_users.find_one(
            {"user_id": user.get("user_id")},
            {"is_admin": 1, "tier": 1, "email": 1, "is_unlimited": 1},
        )
        if row and is_admin_or_founder(row):
            user["is_admin"] = True
            user["tier"] = row.get("tier") or user.get("tier")
            user["is_unlimited"] = bool(row.get("is_unlimited"))
            user["email"] = row.get("email") or user.get("email")
            return user
    raise HTTPException(403, "Admin access required")


__all__ = ["_require_admin"]
