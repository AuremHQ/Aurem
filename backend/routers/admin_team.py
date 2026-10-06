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
from services import admin_team
from services.admin_access_registry import ADMIN_ROUTE_REGISTRY
from services.admin_audit_log import record_event
from services.notifications import emit_notification
from services.usage import founder_emails, is_founder_email

router = APIRouter(prefix="/admin/team", tags=["Admin Team"],
                    dependencies=[Depends(require_admin_dep)])

_VALID_LEVELS = {"off", "view", "work", "manage"}


def _all_screen_keys() -> list[str]:
    return sorted({key for (_m, _p, key, _lvl) in ADMIN_ROUTE_REGISTRY if key})


class CreateAccessRequestBody(BaseModel):
    screen_key: str = Field(..., min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


class ResolveAccessRequestBody(BaseModel):
    action: str = Field(..., pattern="^(approve|deny)$")
    note: str = Field(default="", max_length=500)


async def _founder_user_ids(db) -> list[str]:
    emails = list(founder_emails())
    if not emails:
        return []
    rows = await db.dev_users.find(
        {"email": {"$in": emails}}, {"user_id": 1},
    ).to_list(len(emails))
    return [r["user_id"] for r in rows if r.get("user_id")]


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
    keys = _all_screen_keys()
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


@router.get("/my-access")
async def my_access(authorization: Optional[str] = Header(None)):
    """Self-service (AT3) — any signed-in user can ask "what admin
    access do I have, if any". Registered in MFA_SELF_SERVICE so an
    active team member with zero screen overrides yet can still call
    this (it's how the frontend decides whether to show the "Admin"
    sidebar link at all — see docs/admin-team/STAGE-3.md's AT3 note).
    Never 403s for a signed-in user; returns an empty access map
    instead, so the frontend can treat "no access" as a normal state.
    """
    user = await require_admin(authorization)
    if is_founder_email(user.get("email")):
        return {"ok": True, "is_owner": True, "role": "owner",
                "screens": {k: "manage" for k in _all_screen_keys()}}
    member = await admin_team.get_active_member(user["user_id"])
    if member is None:
        return {"ok": True, "is_owner": False, "role": None, "screens": {}}
    overrides = member.get("overrides") or {}
    screens = {k: lvl for k in _all_screen_keys()
               if (lvl := overrides.get(k, "off")) != "off"}
    return {"ok": True, "is_owner": False, "role": member.get("role"), "screens": screens}


@router.post("/access-requests")
async def create_access_request(body: CreateAccessRequestBody,
                                  authorization: Optional[str] = Header(None)):
    """Self-service (AT3) — a team member who hits an Owner-only
    screen (e.g. a provider secret in Settings) can ask the Owner for
    it instead of just seeing a locked state. This NEVER grants access
    by itself — Owner-only stays hard-locked regardless of overrides
    (see cto_services/auth.py); approving a request is the Owner's own
    signal to go act on it themselves, not an automatic access grant.
    """
    user = await require_admin(authorization)
    db = get_db()
    if is_founder_email(user.get("email")):
        raise HTTPException(400, "The Owner already has full access")
    request_id = uuid.uuid4().hex
    now = _now()
    await db.admin_access_requests.insert_one({
        "request_id": request_id, "user_id": user["user_id"],
        "email": user.get("email"), "screen_key": body.screen_key,
        "note": body.note, "status": "pending",
        "created_at": now, "updated_at": now,
        "resolved_at": None, "resolved_by": None, "resolution_note": None,
    })
    for founder_id in await _founder_user_ids(db):
        await emit_notification(
            db, user_id=founder_id, type="admin_message",
            text=f"{user.get('email')} is requesting access to "
                 f"'{body.screen_key}' (request {request_id[:8]})."
                 + (f" Note: {body.note}" if body.note else ""),
        )
    await record_event(db, actor=user.get("email") or user["user_id"],
                        action="team_access_requested", target=body.screen_key,
                        extra={"request_id": request_id, "note": body.note})
    return {"ok": True, "request_id": request_id}


@router.get("/access-requests")
async def list_access_requests(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    rows = await db.admin_access_requests.find(
        {}, {"_id": 0},
    ).sort("created_at", -1).to_list(200)
    return {"ok": True, "requests": rows}


@router.post("/access-requests/{request_id}/resolve")
async def resolve_access_request(request_id: str, body: ResolveAccessRequestBody,
                                   authorization: Optional[str] = Header(None)):
    owner = await require_admin(authorization)
    db = get_db()
    req = await db.admin_access_requests.find_one({"request_id": request_id, "status": "pending"})
    if not req:
        raise HTTPException(404, "Request not found or already resolved")
    new_status = "approved" if body.action == "approve" else "denied"
    await db.admin_access_requests.update_one(
        {"request_id": request_id},
        {"$set": {"status": new_status, "resolved_at": _now(),
                   "resolved_by": owner.get("email") or owner["user_id"],
                   "resolution_note": body.note, "updated_at": _now()}},
    )
    verb = "approved" if new_status == "approved" else "denied"
    extra = f" — {body.note}" if body.note else ""
    await emit_notification(
        db, user_id=req["user_id"], type="admin_message",
        text=f"The founder {verb} your request for '{req['screen_key']}'{extra}.",
    )
    await record_event(db, actor=owner.get("email") or owner["user_id"],
                        action=f"team_access_request_{new_status}", target=req["email"],
                        extra={"request_id": request_id, "screen_key": req["screen_key"]})
    return {"ok": True, "status": new_status}
