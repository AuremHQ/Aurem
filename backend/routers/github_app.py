"""
routers/github_app.py — Phase 2 · GitHub App install flow

Endpoints (mounted at /api/aurem-dev/github/app/*):

  GET    /github/app/install                — user kickoff (JWT-auth)
  GET    /github/app/callback               — GitHub post-install redirect
                                              (unauth, state-token validated)
  POST   /github/app/webhook                — GitHub POSTs deliveries
                                              (unauth, HMAC-signature-validated)
  GET    /github/app/installations          — list my active installs (JWT-auth)
  DELETE /github/app/installations/{id}     — user disconnect (JWT-auth)

Race semantics:
  Webhook `installation.created` and `/callback` can arrive in either
  order. Both use `find_one_and_update(upsert=True)` with `$set` for
  mutable fields and `$setOnInsert` for immutable ones. Callback
  additionally sets `user_id` (webhook never has it). Result is
  idempotent: whichever arrives first creates the row; the other
  fills in missing fields.

Idempotency:
  Every webhook delivery is deduped by `X-GitHub-Delivery` UUID via
  the `webhook_deliveries` collection (TTL 7d). Repeat deliveries
  return {ok:true, deduped:true} without side-effects.

Security:
  * Callback state token is single-use (atomic
    find_one_and_update({used:false},{$set:{used:true}})).
  * Webhook signature is verified BEFORE any body parsing.
  * DELETE returns 404 (not 403) on cross-user ownership violations
    so installation_id cannot be enumerated.
  * Installation access tokens are never persisted — Phase 1.1 property
    preserved. This router only handles metadata + installation IDs.
"""
# arch: allow-http — direct calls to api.github.com (App metadata,
# installation details, revoke). Uses services/github_app.py primitives
# for JWT signing and installation-token handling.
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db  # noqa: F401 — get_db is a test patch target
from services import github_app as _ga
from services.github_app_config import is_configured as _app_configured
from routers.github_funnel import track_server_side as _funnel_track

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github/app", tags=["GitHub App"])

# State row TTL — user must complete the install within this window.
_STATE_TTL_SECONDS = 15 * 60

# Webhook delivery dedupe collection retention.
_WEBHOOK_DELIVERY_TTL_SECONDS = 7 * 24 * 60 * 60

# Frontend deep-links (relative — the caller's browser resolves against
# the domain they're currently on, so multi-domain deploys just work).
_BRIDGE_PATH             = "/api/aurem-dev/github/app/installed"
_DEEP_LINK_SUCCESS       = _BRIDGE_PATH + "?status=success&install_id={iid}"
_DEEP_LINK_ERR_STATE     = _BRIDGE_PATH + "?status=err&err=invalid_state"
_DEEP_LINK_ERR_PROBE     = _BRIDGE_PATH + "?status=err&err=github_probe_failed"
_DEEP_LINK_APP_PENDING   = _BRIDGE_PATH + "?status=pending"


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════

def _now_utc_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


async def _fetch_installation_meta(installation_id: int) -> dict:
    """`GET /app/installations/{id}` with App-JWT. Returns the full
    installation payload (account, target_type, permissions, events,
    repository_selection, ...)."""
    headers = {
        "Authorization":        f"Bearer {_ga.app_jwt()}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent":           "aurem-github-app/1.0",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"https://api.github.com/app/installations/{installation_id}",
            headers=headers,
        )
    r.raise_for_status()
    return r.json() or {}


def _slim_repo(repo: dict) -> dict:
    return {
        "id":             repo.get("id"),
        "full_name":      repo.get("full_name"),
        "private":        repo.get("private"),
        "default_branch": repo.get("default_branch"),
    }


def _dashboard_url(request: Request, tail: str) -> str:
    """Resolve `tail` against the current request's origin so any
    domain (preview pod, auremcto.com, custom) works. Falls back to
    `tail` alone if headers are unavailable — that's a valid
    same-origin redirect from GitHub's perspective too."""
    try:
        proto = request.headers.get("x-forwarded-proto") or "https"
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host:
            return f"{proto}://{host}{tail}"
    except Exception:
        pass
    import os as _os
    base = (_os.getenv("APP_URL") or "").rstrip("/")
    return f"{base}{tail}" if base else tail


async def _upsert_installation(
    db, installation_id: int, meta: dict,
    *, user_id: Optional[str] = None,
    delivery_id: Optional[str] = None,
    repositories: Optional[list[dict]] = None,
) -> dict:
    """Thin pass-through — the real implementation moved to
    `services/github_app.py::upsert_installation` (2026-09-16) so the
    new admin reconciliation tooling can share it without a
    router-to-router import. Kept as a local alias so every existing
    call site in this file stays unchanged."""
    return await _ga.upsert_installation(
        db, installation_id, meta,
        user_id=user_id, delivery_id=delivery_id, repositories=repositories,
    )


async def _cascade_project_active(
    db, installation_id: int, active: bool,
) -> None:
    """Soft-toggle `installation_active` on every `cto_projects` row
    referencing this installation. Rows are NEVER deleted here — user
    still owns their project data across suspensions."""
    try:
        await db.cto_projects.update_many(
            {"installation_id": installation_id},
            {"$set": {
                "installation_active": active,
                "installation_status_updated_at": _now_utc_epoch(),
            }},
        )
        if not active:
            # P2-A (2026-08-28) — user-facing bell, "repo_revoked"
            # (persistent). One notification per AFFECTED user, not
            # per project, so a user with 3 projects on one revoked
            # installation gets one bell item, not three.
            from services.notifications import emit_notification
            affected = await db.cto_projects.find(
                {"installation_id": installation_id},
                {"user_id": 1, "github_repo": 1},
            ).to_list(length=200)
            seen_users = set()
            for p in affected:
                uid = p.get("user_id")
                if not uid or uid in seen_users:
                    continue
                seen_users.add(uid)
                await emit_notification(
                    db, user_id=uid, type="repo_revoked",
                    text="GitHub access to one of your connected repos was revoked — reconnect it in Settings.")
    except Exception as e:                                        # noqa: