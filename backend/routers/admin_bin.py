"""
routers/admin_bin.py — Iter 212m-171  (Admin Panel Rebuild — Sections 1-5+)

New admin surface introduced by the "AUREM Admin Panel Full Rebuild"
spec:

  Section 1: BIN Tracker  ─  /admin/bin/{bin_id}/projects
                             /admin/users/{bin_id}/tier
  Section 2: Feature Flags ─  /admin/feature-flags/{flag}/user-override  POST+DELETE
                             /admin/feature-flags-sync-env               (boot helper)
  Section 3: LLM Credits   ─  /admin/llm-credits
                             /admin/llm-credit-alert                     POST
  Section 5: Parliament    ─  /admin/parliament/live
  Boundary probes tile     ─  /admin/boundary-probes

Existing admin.py (3680 LOC) is UNTOUCHED — this file is mounted under
the same "/admin" prefix so paths compose naturally.

Reuse:
  • cto_services.auth._require_admin  — every endpoint is admin-gated
  • services.vault._decrypt_pat        — for PAT-validity check
  • services.house_rules               — for advisor_llm slugs
  • routers.cto_projects._decrypt_pat, _user_gh_token — via local import
"""
# arch: allow-http — BIN tracker external API probes (iter 212m-225)
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Depends, Request
from cto_services.auth import require_admin_dep as _require_admin_dep
from pydantic import BaseModel, Field
from services.http import ext_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin / Rebuild"],
                   dependencies=[Depends(_require_admin_dep)])  # Iter 358 router-level gate


# ── Local admin guard (delegates to the shared one) ─────────────────


async def _require_admin(authorization: Optional[str]) -> dict:
    """Wraps the shared admin-required check.

    Iter 212m-230 — Delegates to `cto_services.auth.require_admin`
    (the canonical implementation) instead of the router-side
    `_require_admin` in admin.py.  Removes the last
    `admin_bin → admin → … → main → admin_bin` cycle that
    architecture_health has been flagging.
    """
    from cto_services.auth import require_admin as _svc_require_admin
    return await _svc_require_admin(authorization)


# ────────────────────────────────────────────────────────────────────
# SECTION 1 — BIN TRACKER
# ────────────────────────────────────────────────────────────────────


class TierChangeBody(BaseModel):
    tier: str = Field(..., pattern=r"^(free|starter|pro|team|founder)$")


async def _tier_body_limit(request: Request) -> None:
    """Reject oversized POST bodies (lpdos hardening)."""
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > 1024:
                raise HTTPException(413, "Payload too large")
        except ValueError:
            pass


@router.get("/bin/{bin_id}/projects")
async def bin_tracker_projects(
    bin_id: str,
    authorization: Optional[str] = Header(None),
):
    """List all projects for a BIN with live PAT validity + last activity."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    # Verify user exists
    user = await db.dev_users.find_one({"user_id": bin_id})
    if not user:
        raise HTTPException(404, "BIN not found")

    projs = await db.cto_projects.find(
        {"user_id": bin_id},
        {"_id": 0, "project_id": 1, "name": 1, "github_owner": 1,
         "github_repo": 1, "branch": 1, "github_token": 1,
         "tasks_done": 1, "last_task": 1, "updated_at": 1, "created_at": 1,
         "auth_method": 1, "status": 1,
         "installation_id": 1, "user_id": 1},
    ).to_list(200)

    # Decrypt + probe each PAT in parallel (HEAD /repos/{o}/{r}).
    # 2026-02-11 · Phase 3b (Bug 2 fix) — get_repo_token unifies PAT
    # + github_app auth so App-installed projects show a real token
    # status in the admin probe instead of always "missing".
    # 2026-06 PAT-removal — App-only, no OAuth fallback.
    from services.pat_vault import get_repo_token_or_error

    async def _probe(p):
        pat, _auth_err, _ = await get_repo_token_or_error(p)
        pat_status = "missing"
        pat_last4 = None
        if pat:
            pat_last4 = pat[-4:]
            owner = (p.get("github_owner") or "").strip()
            repo = (p.get("github_repo") or "").strip()
            if owner and repo:
                try:
                    async with ext_client("github", timeout=httpx.Timeout(4.0)) as c:
                        r = await c.head(
                            f"https://api.github.com/repos/{owner}/{repo}",
                            headers={
                                "Authorization": f"Bearer {pat}",
                                "Accept": "application/vnd.github+json",
                            },
                        )
                    if r.status_code == 200:
                        pat_status = "valid"
                    elif r.status_code == 401:
                        pat_status = "invalid"
                    elif r.status_code == 404:
                        pat_status = "repo_not_found"
                    else:
                        pat_status = f"http_{r.status_code}"
                except Exception:
                    pat_status = "probe_error"
            else:
                pat_status = "no_repo"
        # Coerce datetime → iso
        for k in ("updated_at", "created_at", "last_task"):
            v = p.get(k)
            if hasattr(v, "isoformat"):
                p[k] = v.isoformat()
        return {**p, "pat_status": pat_status, "pat_last4": pat_last4,
                "github_token": None}   # never return ciphertext

    rows = []
    for p in projs:
        rows.append(await _probe(p))

    return {
        "ok": True,
        "bin_id": bin_id,
        "email": user.get("email"),
        "tier": user.get("tier", "free"),
        "is_admin": bool(user.get("is_admin")),
        "is_unlimited": bool(user.get("is_unlimited")),
        "project_count": len(rows),
        "projects": rows,
    }


@router.post("/users/{bin_id}/tier", dependencies=[Depends(_tier_body_limit)])
async def change_user_tier(
    bin_id: str,
    body: TierChangeBody,
    authorization: Optional[str] = Header(None),
):
    """Change a user's subscription tier."""
    admin = await _require_admin(authorization)
    # Admin Team S2 (AT1b) / D13 — setting tier="founder" is owner-only
    # and affects PLAN ONLY, never access (D1 already ignores `tier` for
    # the access decision entirely — only FOUNDER_EMAILS does). A team
    # member with Manage on users.plans may still change OTHER tiers.
    if body.tier == "founder":
        from services.usage import is_founder_email
        if not is_founder_email((admin.get("email") or "").strip().lower()):
            raise HTTPException(403, "Only the Owner can set tier=founder")
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    user = await db.dev_users.find_one({"user_id": bin_id})
    if not user:
        raise HTTPException(404, "BIN not found")

    prev_tier = user.get("tier", "free")
    await db.dev_users.update_one(
        {"user_id": bin_id},
        {"$set": {
            "tier": body.tier,
            "