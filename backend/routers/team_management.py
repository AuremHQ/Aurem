"""
routers/team_management.py — 2026-02 · Phase 1 (Team Roles & Permissions)

Real, server-enforced team invites + per-resource access control.
See services/team_permissions.py for the resource list, role presets,
and the `require_permission` gate every other router should reuse.

Endpoints (all under /api/aurem-dev/team):
    GET  /resources               — the resource catalog (for the matrix grid UI)
    GET  /me                      — caller's own resolved role + matrix (nav visibility)
    GET  /members                 — list this account's team (owner/admin)
    POST /invite                  — invite by email (owner/admin, write:team_management)
    PUT  /members/{id}/permissions— custom per-resource overrides (owner/admin)
    POST /members/{id}/resend-invite
    POST /members/{id}/revoke
    GET  /invite/{token}          — PUBLIC — invite preview for the accept-invite page
    POST /accept-invite           — PUBLIC — invited member sets their own password
"""
from __future__ import annotations

import logging
import secrets
import time
import uuid
from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, EmailStr, Field

from cto_services.auth import create_token, current_dev
from cto_services.db import get_db
from services.team_permissions import (
    RESOURCES, ROLE_PRESETS, preset_matrix, require_permission,
    resolve_context, write_audit_log,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/team", tags=["Team & Permissions"])

_INVITE_TTL_S = 7 * 24 * 3600


def _require_db():
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    return db


def _public_member(row: dict) -> dict:
    return {
        "member_id":   row.get("member_id"),
        "email":       row.get("email"),
        "status":      row.get("status"),
        "role_label":  row.get("role_label"),
        "permissions": row.get("permissions"),
        "invited_by":  row.get("invited_by"),
        "created_at":  row.get("created_at"),
        "updated_at":  row.get("updated_at"),
    }


@router.get("/resources")
async def list_resources(authorization: Optional[str] = Header(None)) -> dict:
    await current_dev(authorization)  # any logged-in user may see the catalog
    return {"resources": RESOURCES, "role_presets": {k: v for k, v in ROLE_PRESETS.items()}}


@router.get("/me")
async def my_team_context(authorization: Optional[str] = Header(None)) -> dict:
    """Used by the frontend nav to hide/disable areas the caller can't
    reach — UX only, the real gate is `require_permission` server-side."""
    ctx = await resolve_context(authorization)
    return {
        "is_owner":     ctx["is_owner"],
        "role_label":   ctx["role_label"],
        "permissions":  ctx["permissions"],
        "account_id":   ctx["effective_account_id"],
    }


@router.get("/members")
async def list_members(authorization: Optional[str] = Header(None)) -> dict:
    ctx = await require_permission("team_management", "read")(authorization)
    db = _require_db()
    rows = await db.team_members.find(
        {"owner_id": ctx["effective_account_id"]}, {"_id": 0},
    ).sort("created_at", -1).to_list(200)
    return {"members": [_public_member(r) for r in rows]}


class InviteBody(BaseModel):
    email: EmailStr
    role_label: str = Field("viewer")
    permissions: Optional[dict[str, str]] = None  # custom override on top of the preset


async def _validate_new_invite(db, owner_id: str, self_email: str, invite_email: str) -> None:
    """Raise 400/409 if this invite can't proceed. Split out of
    `invite_member` to keep that function's cyclomatic complexity
    under the new-file-bloat guard's threshold (2026-09-26)."""
    if invite_email == self_email:
        raise HTTPException(400, "You can't invite yourself.")
    existing = await db.team_members.find_one(
        {"owner_id": owner_id, "email": invite_email,
         "status": {"$in": ["pending", "active"]}},
    )
    if existing:
        raise HTTPException(409, "That email is already invited or on the team.")


def _build_invite_matrix(role_label: str, custom_permissions: Optional[dict]) -> dict:
    """Preset first, owner's explicit per-resource overrides layered on
    top (never the other way around, so an override can only be MORE
    specific, never silently dropped by re-applying the preset later).
    `team_management` write stays owner/admin-only no matter what the
    caller sent — enforced here, not just in the preset default."""
    matrix = preset_matrix(role_label)
    for k, v in (custom_permissions or {}).items():
        if k in RESOURCES and v in ("none", "read", "write", "read_write"):
            matrix[k] = v
    if role_label not in ("admin",):
        matrix["team_management"] = "read" if matrix.get("team_management") == "read" else "none"
    return matrix


@router.post("/invite")
async def invite_member(body: InviteBody,
                         authorization: Optional[str] = Header(None)) -> dict:
    ctx = await require_permission("team_management", "write")(authorization)
    db = _require_db()
    owner_id = ctx["effective_account_id"]
    email = body.email.strip().lower()
    self_email = (ctx["user"].get("email") or "").strip().lower()

    await _validate_new_invite(db, owner_id, self_email, email)

    role_label = body.role_label if body.role_label in ROLE_PRESETS else "viewer"
    matrix = _build_invite_matrix(role_label, body.permissions)

    member_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    now = time.time()
    doc = {
        "member_id":        member_id,
        "owner_id":         owner_id,
        "user_id":          None,
        "email":            email,
        "invited_by":       ctx["user"]["user_id"],
        "status":           "pending",
        "role_label":       role_label,
        "permissions":      matrix,
        "invite_token":     token,
        "invite_expires_at": now + _INVITE_TTL_S,
        "created_at":       now,
        "updated_at":       now,
    }
    await db.team_members.insert_one(doc)
    await write_audit_log(
        db, owner_id=owner_id, actor_user_id=ctx["user"]["user_id"],
        target_member_id=member_id, target_email=email,
        action="invite", new_value={"role_label": role_label, "permissions": matrix},
    )
    ok, err = await _send_invite(email, token, ctx["user"].get("email") or "", role_label)
    return {"ok": True, "member_id": member_id, "email_sent": ok, "email_error": err}


async def _send_invite(email: str, token: str, owner_email: str, role_label: str):
    try:
        from services.team_invite_email import send_invite_email
        return await send_invite_email(email, token, owner_email, role_label)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("send_invite_email dispatch failed: %r", e)
        return False, str(e)


class UpdatePermissionsBody(BaseModel):
    permissions: Optional[dict[str, str]] = None
    role_label: Optional[str] = None


@router.put("/members/{member_id}/permissions")
async def update_permissions(member_id: str, body: UpdatePermissionsBody,
                              authorization: Optional[str] = Header(None)) -> dict:
    ctx = await require_permission("team_management", "write")(authorization)
    db = _require_db()
    owner_id = ctx["effective_account_id"]
    row = await db.team_members.find_one({"member_id": member_id, "owner_id": owner_id})
    if not row:
        raise HTTPException(404, "Team member not found")
    # There is no "owner" team_members row — this check exists anyway
    # as defense-in-depth in case that invariant is ever violated.
    if row.get("role_label") == "owner":
        raise HTTPException(403, "The account owner's access can't be changed by anyone else.")

    old_value = {"role_label": row.get("role_label"), "permissions": row.get("permissions")}
    new_matrix = dict(row.get("permissions") or preset_matrix("viewer"))
    new_role = row.get("role_label")
    if body.role_label and body.role_label in ROLE_PRESETS:
        new_role = body.role_label
        new_matrix = preset_matrix(new_role)
    if body.permissions:
        for k, v in body.permissions.items():
            if k in RESOURCES and v in ("none", "read", "write", "read_write"):
                new_matrix[k] = v
        new_role = "custom"

    await db.team_members.update_one(
        {"member_id": member_id},
        {"$set": {"permissions": new_matrix, "role_label": new_role, "updated_at": time.time()}},
    )
    await write_audit_log(
        db, owner_id=owner_id, actor_user_id=ctx["user"]["user_id"],
        target_member_id=member_id, target_email=row.get("email", ""),
        action="update_permissions", old_value=old_value,
        new_value={"role_label": new_role, "permissions": new_matrix},
    )
    return {"ok": True, "role_label": new_role, "permissions": new_matrix}


@router.post("/members/{member_id}/revoke")
async def revoke_member(member_id: str,
                         authorization: Optional[str] = Header(None)) -> dict:
    ctx = await require_permission("team_management", "write")(authorization)
    db = _require_db()
    owner_id = ctx["effective_account_id"]
    row = await db.team_members.find_one({"member_id": member_id, "owner_id": owner_id})
    if not row:
        raise HTTPException(404, "Team member not found")
    if row.get("role_label") == "owner":
        raise HTTPException(403, "The account owner can't be revoked.")
    await db.team_members.update_one(
        {"member_id": member_id},
        {"$set": {"status": "revoked", "updated_at": time.time()}},
    )
    await write_audit_log(
        db, owner_id=owner_id, actor_user_id=ctx["user"]["user_id"],
        target_member_id=member_id, target_email=row.get("email", ""),
        action="revoke", old_value={"status": row.get("status")},
        new_value={"status": "revoked"},
    )
    return {"ok": True}


@router.post("/members/{member_id}/resend-invite")
async def resend_invite(member_id: str,
                         authorization: Optional[str] = Header(None)) -> dict:
    ctx = await require_permission("team_management", "write")(authorization)
    db = _require_db()
    owner_id = ctx["effective_account_id"]
    row = await db.team_members.find_one({"member_id": member_id, "owner_id": owner_id})
    if not row:
        raise HTTPException(404, "Team member not found")
    if row.get("status") != "pending":
        raise HTTPException(400, "Only a pending invite can be resent.")
    token = secrets.token_urlsafe(32)
    now = time.time()
    await db.team_members.update_one(
        {"member_id": member_id},
        {"$set": {"invite_token": token, "invite_expires_at": now + _INVITE_TTL_S,
                   "updated_at": now}},
    )
    ok, err = await _send_invite(row["email"], token, ctx["user"].get("email") or "",
                                  row.get("role_label", "viewer"))
    await write_audit_log(
        db, owner_id=owner_id, actor_user_id=ctx["user"]["user_id"],
        target_member_id=member_id, target_email=row.get("email", ""),
        action="resend_invite",
    )
    return {"ok": True, "email_sent": ok, "email_error": err}


@router.get("/invite/{token}")
async def preview_invite(token: str) -> dict:
    """PUBLIC — the invited person has no account yet, so no auth is
    possible here. Only ever returns the email + role + validity, never
    anything else about the account being invited into."""
    db = _require_db()
    row = await db.team_members.find_one({"invite_token": token}, {"_id": 0})
    if not row or row.get("status") != "pending":
        return {"valid": False}
    if row.get("invite_expires_at", 0) < time.time():
        return {"valid": False, "reason": "expired"}
    return {"valid": True, "email": row["email"], "role_label": row.get("role_label")}


class AcceptInviteBody(BaseModel):
    token: str
    password: str = Field(..., min_length=6)
    name: Optional[str] = None


@router.post("/accept-invite")
async def accept_invite(body: AcceptInviteBody) -> dict:
    """PUBLIC — creates the invited person's own login (their own
    bcrypt-hashed password, their own dev_users row) and links it to
    the inviting account via team_members.user_id. Mirrors the exact
    hashing/JWT primitives routers/auth.py::signup uses."""
    db = _require_db()
    row = await db.team_members.find_one({"invite_token": body.token})
    if not row or row.get("status") != "pending":
        raise HTTPException(400, "Invalid or already-used invite link.")
    if row.get("invite_expires_at", 0) < time.time():
        raise HTTPException(400, "This invite link has expired. Ask the owner to resend it.")

    existing_user = await db.dev_users.find_one({"email": row["email"]})
    if existing_user:
        user_id = existing_user["user_id"]
    else:
        user_id = uuid.uuid4().hex
        hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
        await db.dev_users.insert_one({
            "user_id":  user_id,
            "email":    row["email"],
            "password": hashed,
            "name":     body.name or row["email"].split("@")[0],
            "is_admin": False,
            "is_founder": False,
            "tier":     "team_member",
            "created_at": time.time(),
        })

    await db.team_members.update_one(
        {"member_id": row["member_id"]},
        {"$set": {"status": "active", "user_id": user_id, "updated_at": time.time()},
         "$unset": {"invite_token": "", "invite_expires_at": ""}},
    )
    await write_audit_log(
        db, owner_id=row["owner_id"], actor_user_id=user_id,
        target_member_id=row["member_id"], target_email=row["email"],
        action="invite_accepted",
    )
    token = create_token(user_id=user_id, email=row["email"], is_admin=False)
    return {"ok": True, "token": token, "user": {"email": row["email"], "user_id": user_id}}
