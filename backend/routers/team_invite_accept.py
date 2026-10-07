"""routers/team_invite_accept.py — Admin Team S3 (AT2) invite acceptance.

Deliberately NOT on the admin_team.py router (which requires
`require_admin_dep` at the router level) — the person accepting an
invite is, by definition, not an Owner or existing team member yet.
Only needs a normal logged-in session (`current_dev`); the invite
TOKEN itself is the actual authorization for this one action.

Never signs the accepting user in and never returns a token/session —
they must already be logged into their own existing AUREM account.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.admin_audit_log import record_event
from services.notifications import emit_notification

router = APIRouter(prefix="/team-invite", tags=["Admin Team"])


class AcceptInviteBody(BaseModel):
    token: str = Field(..., min_length=10, max_length=200)


@router.post("/accept")
async def accept_invite(body: AcceptInviteBody, authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    if not user.get("user_id"):
        raise HTTPException(401, "Please log in to accept this invite")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    invite = await db.admin_team_invites.find_one({"token_hash": token_hash, "status": "pending"})
    if not invite:
        raise HTTPException(400, "This invite link is invalid or has already been used")
    accepting_email = (user.get("email") or "").strip().lower()
    if accepting_email != invite["email"]:
        raise HTTPException(
            403,
            "This invite was sent to a different email address. "
            "Please log in with the invited account.",
        )
    now = datetime.now(timezone.utc)
    existing = await db.admin_team_members.find_one({"user_id": user["user_id"]})
    if existing:
        await db.admin_team_members.update_one(
            {"user_id": user["user_id"]},
            {"$set": {"status": "active", "role": invite["role"], "updated_at": now}},
        )
    else:
        await db.admin_team_members.insert_one({
            "user_id": user["user_id"], "email": accepting_email, "role": invite["role"],
            "status": "active", "overrides": {},
            "invited_by": invite["invited_by"],
            "accepted_at": now, "created_at": now, "updated_at": now,
        })
    await db.admin_team_invites.update_one(
        {"invite_id": invite["invite_id"]},
        {"$set": {"status": "accepted", "accepted_by": user["user_id"], "accepted_at": now}},
    )
    await record_event(db, actor=accepting_email, action="team_invite_accepted",
                        target=invite["invited_by"], extra={"role": invite["role"]})
    await emit_notification(
        db, user_id=invite["invited_by"], type="admin_message",
        text=f"{accepting_email} accepted your Admin Team invite and is now a {invite['role']}.",
    )
    # Deliberately no token/session in the response — they keep using
    # their own existing login.
    return {"ok": True, "role": invite["role"]}
