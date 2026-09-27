"""
routers/admin_connect_diagnostic.py — Connect-flow investigation (2026-09-01)

READ-ONLY, admin-gated diagnostic for a SINGLE user_id. Built so the
founder can deploy this to PRODUCTION and run it for each of the 4
named users, then paste the JSON output back for diagnosis — no
production credential ever needs to enter this chat.

Pulls exactly the state named in the investigation brief:
  * github_installations row(s) for the user — installation_id,
    user_id (set/null), repo_ids, granted/denied-relevant timestamps,
    status.
  * cto_projects rows for the user — count + created_at for each, so
    "any created in the failure window" is answerable at a glance.
  * oauth_states rows (kind=github_app_install) for the user — state
    token ISSUED (created_at) vs RECEIVED/consumed (used_at), so a
    callback that never fired is visible as `used=false`.
  * funnel_events (connect_repo_install_failed / first_scan_* —
    Onboarding Step 4 signals) — surfaces any exception the callback
    hit, even though the user saw nothing.
  * The ORDERED combined event stream: github_funnel_events (client +
    server stage instrumentation) + webhook_deliveries (matched by any
    installation_id found for this user) + funnel_events, merged and
    sorted by timestamp — event type, timestamp, event/delivery id,
    installation_id, all in one list so ordering tells real churn
    apart from a bug.

NO WRITES. NO MUTATION. Scoped to exactly the user_id passed in — not
a mass dump. Same admin gate as every other admin-only endpoint
(routers/self_bugs_admin.py, routers/admin_ops_config.py, ...).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header

from cto_services.auth import require_admin, require_admin_dep
from cto_services.db import require_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/connect-diagnostic", tags=["Admin — Connect Flow"],
    dependencies=[Depends(require_admin_dep)],
)


def _iso(v):
    """Best-effort epoch-or-datetime -> ISO string, for readability in
    the pasted JSON output. Leaves non-time values untouched."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return v
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    return v


@router.get("/{user_id}")
async def connect_flow_diagnostic(
    user_id: str,
    authorization: Optional[str] = Header(None),
):
    await require_admin(authorization)
    db = require_db()

    # ── 0. Does this user_id even resolve to a real account? ──────────
    # 2026-09-01 — added after the diagnostic returned all-empty for
    # 2 of the 4 investigated users while their real data WAS visible
    # via /admin/users/{user_id} (the exact same collections/field).
    # An all-empty response for a WRONG/mistyped user_id looks
    # identical to an all-empty response for a real-but-quiet account
    # — that ambiguity is itself a diagnostic gap. Surface it plainly.
    dev_user = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "email": 1, "name": 1, "user_id": 1},
    )

    # ── 1. github_installations rows for this user ────────────────────
    install_rows = await db.github_installations.find(
        {"user_id": user_id},
    ).sort("installed_at", -1).to_list(length=50)
    installations = [
        {
            "installation_id":      r.get("installation_id"),
            "user_id":              r.get("user_id"),
            "github_login":         r.get("github_login"),
            "target_type":          r.get("target_type"),
            "repository_selection": r.get("repository_selection"),
            "repo_ids":             [
                rr.get("id") for rr in (r.get("repositories") or [])
            ],
            "repo_full_names":      [
                rr.get("full_name") for rr in (r.get("repositories") or [])
            ],
            "active":               r.get("active"),
            "installed_at":         _iso(r.get("installed_at")),
            "linked_at":            _iso(r.get("linked_at")),
            "updated_at":           _iso(r.get("updated_at")),
            "suspended_at":         _iso(r.get("suspended_at")),
            "deleted_at":           _iso(r.get("deleted_at")),
            "revoked_at":           _iso(r.get("revoked_at")),
            "last_webhook_delivery": r.get("last_webhook_delivery"),
        }
        for r in install_rows
    ]
    installation_ids = [
        i["installation_id"] for i in installations if i["installation_id"]
    ]

    # ── 2. cto_projects rows for this user ─────────────────────────────
    project_rows = await db.cto_projects.find(
        {"user_id": user_id},
    ).sort("created_at", -1).to_list(length=100)
    projects = [
        {
            "project_id":            r.get("project_id"),
            "github_owner":          r.get("github_owner"),
            "github_repo":           r.get("github_repo"),
            "installation_id":       r.get("installation_id"),
            "installation_active":   r.get("installation_active"),
            "created_at":            _iso(r.get("created_at")),
        }
        for r in project_rows
    ]

    # ── 3. oauth_states (github_app_install) — issued vs received ─────
    state_rows = await db.oauth_states.find(
        {"user_id": user_id, "kind": "github_app_install"},
    ).sort("created_at", -1).to_list(length=50)
    oauth_states = [
        {
            "state_prefix":  (r.get("state") or "")[:12] + "…",  # never full token
            "issued_at":     _iso(r.get("created_at")),
            "expires_at":    _iso(r.get("expires_at")),
            "used":          bool(r.get("used")),
            "used_at":       _iso(r.get("used_at")),
            "funnel_session": r.get("funnel_session"),
        }
        for r in state_rows
    ]

    # ── 4. funnel_events (install-failed / first-scan onboarding) ─────
    funnel_evt_rows = await db.funnel_events.find(
        {"user_id": user_id},
    ).sort("ts_epoch", 1).to_list(length=200)
    funnel_events = [
        {
            "event_type": r.get("event_type"),
            "metadata":   r.get("metadata"),
            "ts":         _iso(r.get("ts_epoch")),
        }
        for r in funnel_evt_rows
    ]

    # ── 5. Ordered combined stream: github_funnel_events + webhooks ───
    gh_funnel_rows = await db.github_funnel_events.find(
        {"user_id": user_id},
    ).sort("ts", 1).to_list(length=300)

    webhook_rows = []
    if installation_ids:
        webhook_rows = await db.webhook_deliveries.find(
            {"installation": {"$in": installation_ids}},
        ).sort("received_at", 1).to_list(length=300)

    combined = []
    for r in gh_funnel_rows:
        combined.append({
            "kind":            "funnel",
            "event_type":      r.get("stage"),
            "origin":          r.get("origin"),      # "client" | "server"
            "source":          r.get("source"),
            "event_id":        r.get("event_id"),
            "installation_id": (r.get("meta") or {}).get("installation_id"),
            "meta":            r.get("meta"),
            "ts":              _iso(r.get("ts")),
            "_sort_ts":        r.get("ts") or 0,
        })
    for r in webhook_rows:
        combined.append({
            "kind":            "webhook",
            "event_type":      r.get("event"),
            "action":          r.get("action"),
            "delivery_id":     r.get("_id"),
            "installation_id": r.get("installation"),
            "ts":              _iso(r.get("received_at")),
            "_sort_ts":        r.get("received_at") or 0,
        })
    for r in funnel_evt_rows:
        combined.append({
            "kind":            "signup_funnel",
            "event_type":      r.get("event_type"),
            "meta":            r.get("metadata"),
            "ts":              _iso(r.get("ts_epoch")),
            "_sort_ts":        r.get("ts_epoch") or 0,
        })
    combined.sort(key=lambda e: e["_sort_ts"])
    for e in combined:
        e.pop("_sort_ts", None)

    return {
        "user_id":                user_id,
        "dev_user_found":         dev_user is not None,
        "dev_user_email":         (dev_user or {}).get("email"),
        "github_installations":   installations,
        "projects": {
            "count": len(projects),
            "rows":  projects,
        },
        "oauth_states":           oauth_states,
        "funnel_events":          funnel_events,
        "ordered_event_stream":   combined,
    }


@router.get("/audit/stale-github-urls")
async def stale_github_urls_audit(authorization: Optional[str] = Header(None)):
    """2026-09-27 P0 — self-service audit: how many `cto_projects` rows
    have a `github_owner/github_repo` that no longer matches ANY repo
    our GitHub App installations can currently see. That mismatch is
    exactly the class of bug behind "my active project points at a
    repo that no longer exists" (Bug 1) — the repo got transferred or
    renamed on GitHub and nothing updated the stored record (the
    webhook handler for `repository` transferred/renamed events did
    not exist before this same fix — see routers/github_app.py).

    Read-only. For each stale row, makes a best-effort live GitHub API
    call to `GET /repos/{old_owner}/{old_repo}` using every installation
    token we hold in turn — GitHub transparently redirects that call to
    the repo's CURRENT identity as long as SOME token we present has
    access to it. Reports the real current `full_name` when found, or
    `not_found_or_no_access` honestly when it isn't — never guesses.
    """
    await require_admin(authorization)
    db = require_db()
    from services.github_app import list_installations, list_installation_repos, \
        get_installation_token, GitHubAppNotConfigured
    import httpx

    try:
        installations = await list_installations()
    except GitHubAppNotConfigured:
        return {"ok": False, "error": "github_app_not_configured"}

    live_full_names: set[str] = set()
    inst_tokens: list[str] = []
    inst_sem = asyncio.Semaphore(15)

    async def _fetch_installation(inst: dict):
        async with inst_sem:
            try:
                repos, (tok, _exp) = await asyncio.gather(
                    list_installation_repos(inst["id"]),
                    get_installation_token(inst["id"]),
                )
                return repos, tok
            except Exception as e:                            # noqa: BLE001
                logger.warning("stale_github_urls_audit: installation %s failed: %r",
                                inst.get("id"), e)
                return [], None

    for repos, tok in await asyncio.gather(*(_fetch_installation(i) for i in installations)):
        live_full_names |= {(r.get("full_name") or "").lower() for r in repos}
        if tok:
            inst_tokens.append(tok)

    projects = await db.cto_projects.find(
        {"github_owner": {"$exists": True, "$ne": ""},
         "github_repo":  {"$exists": True, "$ne": ""}},
        {"_id": 0, "project_id": 1, "user_id": 1, "name": 1,
         "github_owner": 1, "github_repo": 1, "github_url": 1},
    ).to_list(5000)

    candidates = [p for p in projects
                  if f"{p.get('github_owner','')}/{p.get('github_repo','')}".lower()
                  not in live_full_names]

    # 2026-09-27 — bounded concurrency (semaphore) + a short per-call
    # timeout. Doing this sequentially (72 candidates × up to N
    # installation tokens × an 8s timeout each) made the endpoint take
    # minutes; a real admin tool needs to answer inside one HTTP
    # request/UI timeout window.
    sem = asyncio.Semaphore(10)
    client = httpx.AsyncClient(timeout=4.0)

    async def _probe(p: dict) -> dict:
        owner, repo = p.get("github_owner") or "", p.get("github_repo") or ""
        resolved_to = None
        async with sem:
            for tok in inst_tokens:
                try:
                    r = await client.get(
                        f"https://api.github.com/repos/{owner}/{repo}",
                        headers={"Authorization": f"Bearer {tok}",
                                 "Accept": "application/vnd.github+json"},
                    )
                    if r.status_code == 200:
                        live_name = (r.json() or {}).get("full_name")
                        if live_name and live_name.lower() != f"{owner}/{repo}".lower():
                            resolved_to = live_name
                        break
                except Exception:
                    continue
        return {
            "project_id":         p.get("project_id"),
            "user_id":            p.get("user_id"),
            "name":               p.get("name"),
            "stored_url":         p.get("github_url"),
            "stored_owner_repo":  f"{owner}/{repo}",
            "github_redirects_to": resolved_to or "not_found_or_no_access",
        }

    try:
        stale = await asyncio.gather(*(_probe(p) for p in candidates))
    finally:
        await client.aclose()

    return {
        "ok":                True,
        "installations_checked": len(installations),
        "projects_checked":  len(projects),
        "stale_count":       len(stale),
        "stale_projects":    stale,
    }
