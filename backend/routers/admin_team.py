"""routers/admin_team.py — Admin Team S3 (AT2): Team screen + invite flow.

Owner-only management of `admin_team_members` / `admin_team_invites`.
D8 — the invite token is random (`secrets.token_urlsafe`); only its
SHA-256 hash is ever stored. The raw token is returned ONCE (create or
"copy link" regenerate) and never persisted or logged anywhere.
Regenerating a link overwrites the stored hash, so the old token can
never match again (rotation == revocation of the old one).

This is a SEPARATE collection/feature from the existing workspace Team
(`routers/team_management.py`, `db.team_members`) — untouched by this
file.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import require_admin, require_admin_dep
from cto_services.db import get_db
from services.admin_audit_log import record_event

router = APIRouter(prefix="/admin/team", tags=["Admin Team"],
                    dependencies=[Depends(require_admin_dep)])

_VALID_LEVELS = {"off", "view", "work", "manage"}


class CreateInviteBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    role: str = Field(default="developer", max_length=50)


class SetOverridesBody(BaseModel):
    overrides: dict[str, str] = Field(default_factory=dict)


def _now():
    return datetime.now(timezone.utc)


@router.get("/members")
async def list_members(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    rows = await db.admin_team_members.find({}, {"_id": 0}).to_list(500)
    return {"ok": True, "members": rows}


@router.get("/invites")
async def list_pending_invites(authorization: Optional[str] = Header(None)):
    """D8 — token_hash is never returned, even to the Owner."""
    await require_admin(authorization)
    db = get_db()
    rows = await db.admin_team_invites.find(
        {"status": "pending"}, {"_id": 0, "token_hash": 0},
    ).to_list(500)
    return {"ok": True, "invites": rows}


@router.get("/screens")
async def list_screen_keys(authorization: Optional[str] = Header(None)):
    """Distinct screen keys the Owner can grant a level on, for the
    Team screen's override picker."""
    await require_admin(authorization)
    from services.admin_access_registry import ADMIN_ROUTE_REGISTRY
    keys = sorted({key for (_m, _p, key, _lvl) in ADMIN_ROUTE_REGISTRY if key})
    return {"ok": True, "screens": keys, "levels": ["off", "view", "work", "manage"]}


@router.post("/invites")
async def create_invite(body: CreateInviteBody, authorization: Optional[str] = Header(None)):
    owner = await require_admin(authorization)
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid email address")
    db = get_db()
    existing_member = await db.admin_team_members.find_one({"email": email, "status": "active"})
    if existing_member:
        raise HTTPException(409, "This person is already an active team member")
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    invite_id = uuid.uuid4().hex
    now = _now()
    await db.admin_team_invites.insert_one({
        "invite_id": invite_id, "email": email, "token_hash": token_hash,
        "role": body.role, "status": "pending",
        "invited_by": owner["user_id"], "invited_by_email": owner.get("email"),
        "invited_at": now, "created_at": now, "updated_at": now,
    })
    await record_event(db, actor=owner.get("email") or owner["user_id"],
                        action="team_invite_created", target=email,
                        extra={"role": body.role, "invite_id": invite_id})
    # D8 — the raw token is returned exactly once, right here. It is
    # never stored or logged anywhere else in the system.
    return {"ok": True, "invite_id": invite_id, "token": raw_token,
            "email": email, "role": body.role}


@router.post("/invites/{invite_id}/copy-link")
async def regenerate_invite_link(invite_id: str, authorization: Optional[str] = Header(None)):
    """D8 — rotates the token: the OLD raw token (already handed out)
    can never match again once this overwrites its hash."""
    owner = await require_admin(authorization)
    db = get_db()
    invite = await db.admin_team_invites.find_one({"invite_id": invite_id, "status": "pending"})
    if not invite:
        raise HTTPException(404, "Invite not found or no longer pending")
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await db.admin_team_invites.update_one(
        {"invite_id": invite_id},
        {"$set": {"token_hash": token_hash, "updated_at": _now()}},
    )
    await record_event(db, actor=owner.get("email") or owner["user_id"],
                        action="team_invite_link_regenerated", target=invite["email"],
                        extra={"invite_id": invite_id})
    return {"ok": True, "invite_id": invite_id, "token": raw_token}


@router.post("/invites/{invite_id}/cancel")
async def cancel_invite(invite_id: str, authorization: Optional[str] = Header(None)):
    owner = await require_admin(authorization)
    db = get_db()
    invite = await db.admin_team_invites.find_one({"invite_id": invite_id, "status": "pending"})
    if not invite:
        raise HTTPException(404, "Invite not found or no longer pending")
    await db.admin_team_invites.update_one(
        {"invite_id": invite_id},
        {"$set": {"status": "cancelled", "updated_at": _now()}},
    )
    await record_event(db, actor=owner.get("email") or owner["user_id"],
                        action="team_invite_cancelled", target=invite["email"],
                        extra={"invite_id": invite_id})
    return {"ok": True}


@router.post("/members/{user_id}/suspend")
async def suspend_member(user_id: str, authorization: Optional[str] = Header(None)):
    owner = await require_admin(authorization)
    db = get_db()
    r = await db.admin_team_members.update_one(
        {"user_id": user_id, "status": "active"},
        {"$set": {"status": "suspended", "updated_at": _now()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Active team member not found")
    await record_event(db, actor=owner.get("email") or owner["user_id"],
                        action="team_member_suspended", target=user_id)
    return {"ok": True}


@router.post("/members/{user_id}/resume")
async def resume_member(user_id: str, authorization: Optional[str] = Header(None)):
    owner = await require_admin(authorization)
    db = get_db()
    r = await db.admin_team_members.update_one(
        {"user_id": user_id, "status": "suspended"},
        {"$set": {"status": "active", "updated_at": _now()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Suspended team member not found")
    await record_event(db, actor=owner.get("email") or owner["user_id"],
                        action="team_member_resumed", target=user_id)
    return {"ok": True}


@router.post("/members/{user_id}/remove")
async def remove_member(user_id: str, authorization: Optional[str] = Header(None)):
    owner = await require_admin(authorization)
    db = get_db()
    r = await db.admin_team_members.delete_one({"user_id": user_id})
    if r.deleted_count == 0:
        raise HTTPException(404, "Team member not found")
    await record_event(db, actor=owner.get("email") or owner["user_id"],
                        action="team_member_removed", target=user_id)
    return {"ok": True}


@router.patch("/members/{user_id}/overrides")
async def set_member_overrides(user_id: str, body: SetOverridesBody,
                                authorization: Optional[str] = Header(None)):
    owner = await require_admin(authorization)
    for screen_key, level in body.overrides.items():
        if level not in _VALID_LEVELS:
            raise HTTPException(400, f"Invalid level '{level}' for screen '{screen_key}'")
    db = get_db()
    r = await db.admin_team_members.update_one(
        {"user_id": user_id},
        {"$set": {"overrides": body.overrides, "updated_at": _now()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Team member not found")
    await record_event(db, actor=owner.get("email") or owner["user_id"],
                        action="team_member_overrides_changed", target=user_id,
                        extra={"overrides": body.overrides})
    return {"ok": True}
