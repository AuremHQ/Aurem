"""services/admin_team.py — Admin Team S2 (AT1b)

Two collections:

  admin_team_members  — ACCEPTED members only, keyed by `user_id` (D7).
    { user_id, email (lowercase, display-only — NOT the lookup key),
      role, status: "active"|"suspended", overrides: {screen_key: level},
      invited_by, invited_at, accepted_at, created_at, updated_at }
    Per D21, nothing here ever implies `dev_users.is_admin = true` — a
    team member's `dev_users` row keeps `is_admin: false`.

  admin_roles — role_key -> default screen levels + sensitive-data
    switches. Seeded lazily with a secure-by-default "developer" role
    (nothing granted) the first time it's read if no DB row exists yet;
    the Owner configures real levels later via the Team screen (AT2/S3).

`effective_level()` is what the gate (`cto_services.auth.require_admin`)
calls to decide if a team member's role+overrides satisfy a route's
required level from `services/admin_access_registry.py`.
"""
from __future__ import annotations

from typing import Optional

from cto_services.db import get_db

OFF, VIEW, WORK, MANAGE = 0, 1, 2, 3
_LEVEL_RANK = {
    "off": OFF, "view": VIEW, "work": WORK, "manage": MANAGE,
    OFF: OFF, VIEW: VIEW, WORK: WORK, MANAGE: MANAGE,
}

# Secure-by-default: a freshly-seeded "developer" role grants nothing
# until the Owner explicitly assigns screen levels via the Team screen
# (AT2/S3) or a per-person override.
DEFAULT_ROLES: dict[str, dict] = {
    "developer": {"role_key": "developer", "screens": {}, "sensitive_data": {}},
}


def _rank(level) -> int:
    return _LEVEL_RANK.get(level, OFF)


async def get_active_member(user_id: str) -> Optional[dict]:
    """D7 — accepted members are looked up by user_id only."""
    if not user_id:
        return None
    db = get_db()
    if db is None:
        return None
    return await db.admin_team_members.find_one(
        {"user_id": user_id, "status": "active"},
    )


async def get_role(role_key: str) -> dict:
    db = get_db()
    if db is not None:
        row = await db.admin_roles.find_one({"role_key": role_key})
        if row:
            return row
    return DEFAULT_ROLES.get(role_key, {"screens": {}, "sensitive_data": {}})


async def effective_level(member: dict, screen_key: str) -> int:
    """Per-person override wins outright over the role's default for
    that screen (D-series spec: "per-person overrides")."""
    overrides = member.get("overrides") or {}
    if screen_key in overrides:
        return _rank(overrides[screen_key])
    role = await get_role(member.get("role", "developer"))
    screens = role.get("screens") or {}
    return _rank(screens.get(screen_key))
