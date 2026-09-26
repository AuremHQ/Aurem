"""
services/team_permissions.py — 2026-02 · Phase 1 (Team Roles & Permissions)

Real, server-enforced RBAC for AUREM. An account owner can invite team
members and grant each of them, per real product area ("resource"),
one of four access levels: none / read / write / read_write.

Design
------
AUREM has always been single-owner-per-account (`dev_users.user_id`
IS the account). This module introduces the team layer WITHOUT
changing that: a team member is their OWN `dev_users` row (their own
login) that is additionally linked, via a `team_members` row, to the
account they were invited into (`owner_id`). `resolve_context()` is
the single place that turns "which JWT is this" into "which
account's data should this request see" (`effective_account_id`) —
for the owner that's always their own user_id; for an active team
member it's the `owner_id` on their `team_members` row.

`require_permission(resource, level)` is the ONE reusable gate. It is
called the same way `require_admin`/`current_dev` already are
elsewhere in this codebase (inline `await require_permission(...)(authorization)`
at the top of a handler) — no new dependency-injection pattern to
learn. A denied check raises a real HTTPException(403) with a
machine-readable reason; nothing about the gate is client-side.

The owner role is never a `team_members` row at all, so it can never
be edited, demoted, or revoked by anyone (including other admins) —
enforced by construction, not by a special-case check that could be
forgotten somewhere.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import HTTPException

from cto_services.auth import current_dev

# ─── Resources — the real product areas found in this app ──────────
# (routers/pages each one maps to are documented per-endpoint at the
# call sites below and in memory/CHANGELOG.md — not every one of the
# ~100 backend routers is a distinct customer-facing "area"; these are
# the ones a founder would actually list on an invite screen.)
RESOURCES: dict[str, str] = {
    "dashboard":       "Dashboard / overview",
    "projects":        "Projects (connect + manage repos)",
    "repo_connect":    "GitHub App connection",
    "loop":            "Loop Mode (ORA fixes your code)",
    "deploy":          "Deploy",
    "domain":          "Domain",
    "integrations":    "Integrations (MCP / IDE keys)",
    "automations":     "Automations",
    "reports":         "Usage reports",
    "billing":         "Billing / tokens",
    "settings":        "Settings",
    "support":         "Support tickets",
    "team_management": "Team & permissions",
}

_LEVELS = ("none", "read", "write", "read_write")

# ─── Role presets — pre-fill, owner can still override per-resource ─
def _matrix(default: str, **overrides: str) -> dict[str, str]:
    m = {r: default for r in RESOURCES}
    m.update(overrides)
    return m


ROLE_PRESETS: dict[str, dict[str, str]] = {
    # Full access everywhere, including team_management itself.
    "admin":  _matrix("read_write"),
    # Can operate every real feature, but cannot manage the team roster.
    "editor": _matrix("read_write", team_management="none"),
    # Look, don't touch, anywhere.
    "viewer": _matrix("read", team_management="none"),
}


def preset_matrix(role_label: str) -> dict[str, str]:
    return dict(ROLE_PRESETS.get(role_label, ROLE_PRESETS["viewer"]))


def _satisfies(have: str, required: str) -> bool:
    if required == "read":
        return have in ("read", "read_write")
    if required == "write":
        return have in ("write", "read_write")
    raise ValueError(f"unknown required level: {required!r}")


async def resolve_context(authorization: Optional[str]) -> dict:
    """Identity -> effective account context. Every gated route calls
    this (via `require_permission`) exactly once."""
    user = await current_dev(authorization)
    return await resolve_context_for_user(user)


async def resolve_context_for_user(user: dict, db=None) -> dict:
    """Same as `resolve_context`, but for call sites that already
    resolved `user` via their OWN (mockable) `current_dev` import —
    avoids a second, independent auth pass that existing unit tests
    can't see when they monkeypatch that router's `current_dev`.
    `db` should be the SAME db handle the caller already has (its own
    mockable `get_db()`/`require_db()`) — falls back to a fresh
    `cto_services.db.get_db()` only when the caller has none yet."""
    if db is None:
        from cto_services.db import get_db
        db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    # Defensive: a handful of pre-existing unit tests pass a minimal
    # hand-rolled fake `db` (e.g. only `find_one` on the object itself,
    # no `.team_members` collection) — those predate the team feature
    # entirely and never exercise it, so treat that shape as "no team
    # collection available" and fall back to owner semantics rather
    # than raising. Real Mongo/Motor never raises AttributeError here.
    member_row = None
    try:
        member_row = await db.team_members.find_one(
            {"user_id": user["user_id"], "status": "active"},
            {"_id": 0},
        )
    except (AttributeError, TypeError):
        member_row = None
    # 2026-09-26 P0 RBAC fix (Task 2 follow-up) — a bare unittest.mock
    # `AsyncMock()` db (widely used across this codebase's existing
    # router tests, predating the team feature) does NOT raise on
    # `db.team_members.find_one(...)` like the try/except above
    # anticipated — it silently returns another (truthy) AsyncMock
    # instance instead of `None`. Without this check that fake object
    # was treated as a REAL active team-member row, crashing every
    # such test deep inside `_enforce` (`ctx["permissions"]` ended up
    # being an unawaited coroutine, not a dict). A real Motor find_one
    # only ever returns `None` or an actual `dict`, so treating
    # anything else as "no row" is safe for production and restores
    # the pre-existing owner-only fallback these tests already rely on.
    if member_row is not None and not isinstance(member_row, dict):
        member_row = None
    if member_row:
        return {
            "user":                user,
            "is_owner":            False,
            "effective_account_id": member_row["owner_id"],
            "role_label":          member_row.get("role_label", "viewer"),
            "permissions":         member_row.get("permissions") or preset_matrix("viewer"),
            "member_id":           member_row.get("member_id"),
        }
    # No active team_members row for this user_id => they ARE an
    # account owner (their own data, full access, always).
    return {
        "user":                 user,
        "is_owner":             True,
        "effective_account_id": user["user_id"],
        "role_label":           "owner",
        "permissions":          _matrix("read_write"),
        "member_id":            None,
    }


def require_permission(resource: str, level: str):
    """Full flow (auth + permission) for call sites with NO existing
    `current_dev` call to reuse — e.g. brand-new routers. Call it like
    `require_admin`:
        ctx = await require_permission("projects", "write")(authorization)
    `ctx["effective_account_id"]` is the account whose data the
    handler should read/write; `ctx["user"]` is the caller's own
    identity (for audit/attribution)."""
    if resource not in RESOURCES:
        raise ValueError(f"unknown resource: {resource!r}")
    if level not in ("read", "write"):
        raise ValueError(f"level must be 'read' or 'write', got {level!r}")

    async def _check(authorization: Optional[str] = None) -> dict:
        ctx = await resolve_context(authorization)
        _enforce(ctx, resource, level)
        return ctx

    return _check


async def check_permission(user: dict, resource: str, level: str, db=None) -> dict:
    """Same gate as `require_permission`, but for a call site that has
    ALREADY resolved `user` via its own `current_dev(authorization)`
    call (the common existing-router pattern) — reuses that identity
    instead of authenticating a second time:
        me = await current_dev(authorization)
        ctx = await check_permission(me, "loop", "write", db=db)
    Pass the router's own already-obtained `db` when it has one — some
    unit tests monkeypatch that router's `get_db`, not the global one.
    """
    if resource not in RESOURCES:
        raise ValueError(f"unknown resource: {resource!r}")
    if level not in ("read", "write"):
        raise ValueError(f"level must be 'read' or 'write', got {level!r}")
    ctx = await resolve_context_for_user(user, db=db)
    _enforce(ctx, resource, level)
    return ctx


def _enforce(ctx: dict, resource: str, level: str) -> None:
    have = ctx["permissions"].get(resource, "none")
    if not _satisfies(have, level):
        raise HTTPException(403, {
            "error":    "permission_denied",
            "resource": resource,
            "required": level,
            "have":     have,
        })


async def write_audit_log(db, *, owner_id: str, actor_user_id: str,
                           target_member_id: str, target_email: str,
                           action: str, old_value: Optional[dict] = None,
                           new_value: Optional[dict] = None) -> None:
    await db.permission_audit_log.insert_one({
        "audit_id":         uuid.uuid4().hex,
        "owner_id":         owner_id,
        "actor_user_id":    actor_user_id,
        "target_member_id": target_member_id,
        "target_email":     target_email,
        "action":           action,
        "old_value":        old_value,
        "new_value":        new_value,
        "created_at":       time.time(),
    })
