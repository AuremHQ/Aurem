"""
routers/onboarding.py — Admin trigger + public click-tracking.

Two endpoints:
  • POST /api/aurem-dev/admin/onboarding/send-connect-nudge
      Admin-only. No per-call cap (user explicitly requested "direct
      send, no cap"). Supports `dry_run=true` to preview the batch
      without sending. Supports `user_ids=[...]` to scope a manual
      send to a subset.
  • GET  /api/aurem-dev/onboarding/click?uid=X&c=connect_repo_nudge
      Public 302 redirector. Logs the click against the most recent
      onboarding_emails row for that user, then bounces them to the
      dashboard with `?action=connect-repo&utm_source=email&utm_campaign=onboarding`
      so the wizard opens automatically.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.onboarding_email import (
    CAMPAIGN, eligible_users, run_nudge_batch,
)
from services.signup_guards import emit_funnel_event
from services.web_skills import _firecrawl_key, _is_blocked, FIRECRAWL_BASE

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Onboarding"])


# ── Admin trigger ────────────────────────────────────────────────────
class _NudgeBody(BaseModel):
    dry_run:  bool = False
    stages:   list[str] = ["t24", "t72"]
    user_ids: Optional[list[str]] = None


@router.post("/admin/onboarding/send-connect-nudge")
async def send_connect_nudge(
    body: _NudgeBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger the connect-repo nudge campaign. Admin/founder only.

    `dry_run=true` returns the would-be recipients and a preview of
    the rendered body without calling Resend or writing to Mongo (no
    audit row, no email)."""
    me = await current_dev(authorization)
    if not (me.get("is_admin") or me.get("tier") == "founder"):
        raise HTTPException(403, "Admin access required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    # Validate stage names defensively.
    stages = tuple(s for s in body.stages if s in ("t24", "t72"))
    if not stages:
        raise HTTPException(400, "stages must contain t24 and/or t72")

    if body.dry_run:
        # Preview path — no audit row, no send.
        preview = []
        for stage in stages:
            cohort = await eligible_users(db, stage=stage)
            if body.user_ids:
                keep = set(body.user_ids)
                cohort = [u for u in cohort if u.get("user_id") in keep]
            for u in cohort:
                preview.append({
                    "user_id": u.get("user_id"),
                    "email":   u.get("email"),
                    "name":    u.get("name"),
                    "stage":   stage,
                })
        return {
            "ok":         True,
            "dry_run":    True,
            "stages":     list(stages),
            "recipients": preview,
            "count":      len(preview),
        }

    result = await run_nudge_batch(
        db, stages=stages, dry_run=False,
        user_ids=body.user_ids,
    )
    return result


class _GateStuckNudgeBody(BaseModel):
    dry_run:  bool = False
    user_ids: Optional[list[str]] = None


@router.post("/admin/onboarding/send-gate-stuck-nudge")
async def send_gate_stuck_nudge_admin(
    body: _GateStuckNudgeBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Manual trigger for the fast gate-stuck nudge (services/
    gate_stuck_nudge.py) — same runs automatically on an hourly cron,
    this lets an admin preview the cohort or force a run immediately
    instead of waiting for the next tick. Admin/founder only."""
    me = await current_dev(authorization)
    if not (me.get("is_admin") or me.get("tier") == "founder"):
        raise HTTPException(403, "Admin access required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    from services.gate_stuck_nudge import stuck_gate_users, run_gate_stuck_nudge_batch, send_gate_stuck_nudge

    if body.dry_run:
        cohort = await stuck_gate_users(db)
        if body.user_ids:
            keep = set(body.user_ids)
            cohort = [u for u in cohort if u.get("user_id") in keep]
        recipients = []
        for u in cohort:
            preview = await send_gate_stuck_nudge(u, dry_run=True)
            recipients.append({
                "user_id": u.get("user_id"), "email": u.get("email"),
                "name": u.get("name"), "preview": preview.get("preview"),
            })
        return {"ok": True, "dry_run": True, "recipients": recipients, "count": len(recipients)}

    if body.user_ids:
        cohort = [u for u in await stuck_gate_users(db) if u.get("user_id") in set(body.user_ids)]
        summary = {"ok": True, "dry_run": False, "sent": 0, "skipped": 0, "failed": 0,
                   "recipients": [], "errors": []}
        for u in cohort:
            res = await send_gate_stuck_nudge(u, dry_run=False)
            summary["recipients"].append(res)
            if res.get("skipped"):
                summary["skipped"] += 1
            elif res.get("ok"):
                summary["sent"] += 1
            else:
                summary["failed"] += 1
        return summary

    return await run_gate_stuck_nudge_batch(db, dry_run=False)


# ── Public click tracker ─────────────────────────────────────────────
@router.get("/onboarding/click")
async def onboarding_click(
    uid: str = "",
    c:   str = CAMPAIGN,
    stage: Optional[str] = None,
):
    """Public 302 — logs the click and redirects to the dashboard.

    Always returns a redirect, even when `uid` is missing or the
    audit row can't be located, so a malformed link still drops the
    user on the right page rather than showing an error.

    2026-08-20 — `stage` is optional and used by the newer
    `funnel_stage_nudge` campaign (services/funnel_nudge_cron.py) so
    a click attributes to the exact stage email that was sent (a user
    can receive several different stage nudges over their lifetime,
    unlike the old campaign's single t24/t72 pair). It also decides
    the redirect target: the two GitHub-related stages reopen the
    connect-repo wizard; `stage3_no_chat` users already have a
    project, so they land on the plain dashboard instead.
    """
    action = "connect-repo"
    if stage == "stage3_no_chat":
        action = None

    target_path = "/dashboard?" + urlencode({
        **({"action": action} if action else {}),
        "utm_source":   "email",
        "utm_campaign": c,
    })

    public_base = os.environ.get(
        "PUBLIC_APP_URL", "https://auremcto.com",
    ).rstrip("/")
    target = f"{public_base}{target_path}"

    db = get_db()
    if db is not None and uid:
        try:
            # Latest sent row for this user+campaign(+stage when given).
            # Idempotent: if the user clicks twice we bump
            # `click_count` and update `last_clicked_at`, but
            # `clicked_at` (first-click) sticks.
            now = datetime.now(timezone.utc)
            query: dict = {"user_id": uid, "campaign": c, "sent_ok": True}
            if stage:
                query["stage"] = stage
            doc = await db.onboarding_emails.find_one(
                query, sort=[("sent_at", -1)],
            )
            if doc:
                update: dict = {
                    "$set": {"last_clicked_at": now},
                    "$inc": {"click_count": 1},
                }
                if not doc.get("clicked_at"):
                    update["$set"]["clicked_at"] = now
                await db.onboarding_emails.update_one(
                    {"_id": doc["_id"]}, update,
                )
        except Exception as e:
            logger.warning("onboarding click log failed: %r", e)

    return RedirectResponse(url=target, status_code=302)


# ═════════════════════════════════════════════════════════════════════
# Mandatory Repo-Connect Gate + Post-Connect Engagement Nudge
# (2026-09-09) — founder ticket, see PRD.md for the full spec.
#
# Part 1 state model (deliberately NOT a 3-valued enum in the DB —
# it's a pure function of two independently-true facts, re-checked
# fresh on every chat load):
#   has_project   -> Case A already happened, gate never shows again.
#   gate_skipped  -> Case B happened, gate never shows again.
#   neither       -> Case C (never asked, or abandoned mid-gate) —
#                    gate shows. No extra "abandon" bookkeeping needed:
#                    closing the tab/refreshing just means neither
#                    flag ever got set, so the SAME check re-shows the
#                    gate next time with zero special-case code.
# ═════════════════════════════════════════════════════════════════════

_POST_SKIP_NUDGE_LIFETIME_CAP = 3
_POST_SCAN_FALLBACK_TASK = "Fix the top issue from your scan"


class _GateCompletedBody(BaseModel):
    project_id: Optional[str] = None


class _NudgeShownBody(BaseModel):
    trigger: str  # "post_skip" | "post_scan"
    project_id: Optional[str] = None


@router.get("/onboarding/repo-connect-gate/state")
async def repo_connect_gate_state(authorization: Optional[str] = Header(None)) -> dict:
    """One round-trip for the frontend's gate decision: has this user
    already skipped (Case B) or already connected a repo (Case A)?
    If neither, the gate must show on this chat load."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"skipped": False, "has_project": False, "post_skip_nudge_count": 0}
    user = await db.dev_users.find_one(
        {"user_id": me["user_id"]},
        {"_id": 0, "repo_connect_gate_skipped": 1, "post_skip_nudge_count": 1},
    ) or {}
    has_project = await db.cto_projects.count_documents({"user_id": me["user_id"]}) > 0
    return {
        "skipped": bool(user.get("repo_connect_gate_skipped")),
        "has_project": has_project,
        "post_skip_nudge_count": int(user.get("post_skip_nudge_count") or 0),
    }


@router.post("/onboarding/repo-connect-gate/shown")
async def repo_connect_gate_shown(authorization: Optional[str] = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = get_db()
    if db is not None:
        from services.signup_guards import emit_repo_connect_gate_shown
        await emit_repo_connect_gate_shown(db, user_id=me["user_id"])
    return {"ok": True}


@router.post("/onboarding/repo-connect-gate/skip")
async def repo_connect_gate_skip(authorization: Optional[str] = Header(None)) -> dict:
    """Explicit "Skip for now" click on the mandatory gate (Case B).
    Idempotent: only flips the flag + emits the funnel event the
    FIRST time — the gate never shows again once skipped, so a
    second skip call shouldn't normally happen, but a stray retry
    must not double-log."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True}
    updated = await db.dev_users.find_one_and_update(
        {"user_id": me["user_id"], "repo_connect_gate_skipped": {"$ne": True}},
        {"$set": {"repo_connect_gate_skipped": True}},
        projection={"_id": 0, "user_id": 1},
    )
    if updated:
        from services.signup_guards import emit_repo_connect_gate_skipped
        await emit_repo_connect_gate_skipped(db, user_id=me["user_id"])
    return {"ok": True}


@router.post("/onboarding/repo-connect-gate/completed")
async def repo_connect_gate_completed(
    body: _GateCompletedBody, authorization: Optional[str] = Header(None),
) -> dict:
    """Fired by the gate-mode wizard right after a successful connect
    (Case A). Pure funnel-measurement signal — the gate stops showing
    on its own once `has_project` is true; this event is what tells
    us it was specifically THIS screen that got them there."""
    me = await current_dev(authorization)
    db = get_db()
    if db is not None:
        from services.signup_guards import emit_repo_connect_gate_completed
        await emit_repo_connect_gate_completed(
            db, user_id=me["user_id"], project_id=body.project_id,
        )
    return {"ok": True}


@router.post("/onboarding/nudge/shown")
async def engagement_nudge_shown(
    body: _NudgeShownBody, authorization: Optional[str] = Header(None),
) -> dict:
    """Part 2 — a nudge just rendered in-chat. Records it as the
    'pending' nudge so the next real task message can be attributed
    to it (post_scan_nudge_task_sent). For the post-skip variant,
    also enforces the hard lifetime cap of 3 server-side (frontend
    caps too, but the server is the source of truth)."""
    if body.trigger not in ("post_skip", "post_scan"):
        raise HTTPException(400, "trigger must be post_skip or post_scan")
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "capped": False}
    if body.trigger == "post_skip":
        user = await db.dev_users.find_one(
            {"user_id": me["user_id"]}, {"_id": 0, "post_skip_nudge_count": 1},
        ) or {}
        if int(user.get("post_skip_nudge_count") or 0) >= _POST_SKIP_NUDGE_LIFETIME_CAP:
            return {"ok": True, "capped": True}
        await db.dev_users.update_one(
            {"user_id": me["user_id"]}, {"$inc": {"post_skip_nudge_count": 1}},
        )
    await db.dev_users.update_one(
        {"user_id": me["user_id"]},
        {"$set": {"pending_nudge": {
            "trigger": body.trigger, "shown_at": time.time(),
            "project_id": body.project_id, "fired": False,
        }}},
    )
    from services.signup_guards import emit_engagement_nudge_shown
    await emit_engagement_nudge_shown(
        db, user_id=me["user_id"], trigger=body.trigger, project_id=body.project_id,
    )
    return {"ok": True, "capped": False}


@router.get("/onboarding/nudge/post-scan-state")
async def post_scan_nudge_state(
    project_id: Optional[str] = None, authorization: Optional[str] = Header(None),
) -> dict:
    """Part 2 trigger condition 2: connected + ran a scan or opened
    the graph, but zero real task messages sent since. `prefill_task`
    pulls the real scan's `top_category`; if that's missing for any
    reason, ships the exact named fallback task — never a blank or
    generic placeholder. Frontend wraps it as `Try: '{prefill_task}'`
    for display and uses it verbatim to prefill the chat composer."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"show": False, "prefill_task": None}

    latest = await db.funnel_events.find(
        {"user_id": me["user_id"], "event_type": {"$in": ["first_scan_completed", "graph_built"]}},
        {"_id": 0, "ts_epoch": 1},
    ).sort("ts_epoch", -1).limit(1).to_list(1)
    if not latest:
        return {"show": False, "prefill_task": None}
    scan_ts = latest[0].get("ts_epoch") or 0

    user = await db.dev_users.find_one(
        {"user_id": me["user_id"]}, {"_id": 0, "last_real_task_message_at": 1},
    ) or {}
    last_task_at = user.get("last_real_task_message_at") or 0
    if last_task_at >= scan_ts:
        return {"show": False, "prefill_task": None}

    top_scan = await db.funnel_events.find_one(
        {"user_id": me["user_id"], "event_type": "first_scan_completed"},
        {"_id": 0, "metadata": 1}, sort=[("ts_epoch", -1)],
    )
    top_category = ((top_scan or {}).get("metadata") or {}).get("top_category")
    prefill_task = (
        f"Fix the top issue you found — {top_category}"
        if top_category else _POST_SCAN_FALLBACK_TASK
    )
    return {"show": True, "prefill_task": prefill_task}



# ── Guided first task — website scan (2026-09-28 MASTER TASK ─────────
# "ACTIVATION FIRST" Part 3) ───────────────────────────────────────────
# This is the actual scan TRIGGER referenced above (top of file docs
# for /onboarding/nudge/post-scan-state say "the trigger itself
# doesn't exist yet" — it does now). Deliberately its OWN event names
# (website_scan_started/completed), NOT first_scan_started/completed —
# those require a `project_id` (a connected-repo scan/graph-build,
# a different feature); this is for users with NO repo at all.
class ScanBody(BaseModel):
    url: str = Field(..., min_length=4, max_length=500)


def _normalize_scan_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise HTTPException(400, "URL is required")
    if not re.match(r"^https?://", raw, re.I):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    # Reject garbage ("not a url at all" etc.) before it ever reaches
    # httpx — must look like a real hostname: letters/digits/hyphens,
    # at least one dot (or "localhost"), no spaces.
    host = (parsed.hostname or "")
    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", host, re.I) \
            and host != "localhost":
        raise HTTPException(400, "That doesn't look like a valid website address")
    return raw


async def _scan_via_firecrawl(url: str) -> Optional[dict]:
    key = _firecrawl_key()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)) as c:
            r = await c.post(
                f"{FIRECRAWL_BASE}/scrape",
                json={"url": url, "formats": ["markdown"]},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
    except (httpx.TimeoutException, httpx.RequestError) as e:
        logger.info("[onboarding-scan] firecrawl network error, falling back: %r", e)
        return None
    if r.status_code >= 400:
        logger.info("[onboarding-scan] firecrawl %s, falling back: %s", r.status_code, r.text[:200])
        return None
    body = r.json() or {}
    data = body.get("data") or {}
    if not body.get("success", True) or not data.get("markdown"):
        return None
    title = (data.get("metadata") or {}).get("title") or ""
    md = data.get("markdown") or ""
    issues = []
    if not title:
        issues.append({"severity": "medium", "title": "No page title found",
                        "detail": "Search engines and browser tabs show a blank title."})
    if len(md.strip()) < 200:
        issues.append({"severity": "high", "title": "Almost no visible content",
                        "detail": "Very little readable text was found on this page."})
    return {"scan_path": "firecrawl", "title": title, "issues": issues}


async def _scan_via_fallback(url: str) -> dict:
    """Plain HTTP fetch + our own static checks — runs whenever
    Firecrawl is unavailable (exhausted credits, down, no key), so a
    website scan NEVER comes back empty-handed."""
    if _is_blocked(url):
        raise HTTPException(400, "That URL isn't allowed (internal/private address)")
    issues = []
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=5.0),
                                      follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "ORA-Scanner/1.0 (+https://auremcto.com)"})
    except httpx.TimeoutException:
        raise HTTPException(502, "That site took too long to respond (12s timeout)")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Couldn't reach that site: {e}")
    elapsed_ms = round((time.monotonic() - t0) * 1000)

    if r.status_code >= 400:
        issues.append({"severity": "high", "title": f"Site returned HTTP {r.status_code}",
                        "detail": "Visitors (and search engines) will see an error page."})
    if not url.startswith("https://"):
        issues.append({"severity": "high", "title": "Not using HTTPS",
                        "detail": "Browsers flag non-HTTPS sites as \"Not secure\"."})
    if elapsed_ms > 3000:
        issues.append({"severity": "medium", "title": f"Slow response ({elapsed_ms}ms)",
                        "detail": "Pages over ~3s to respond lose visitors and rank lower in search."})

    soup = BeautifulSoup(r.text or "", "html.parser")
    title_tag = soup.find("title")
    if not title_tag or not (title_tag.text or "").strip():
        issues.append({"severity": "medium", "title": "No page title found",
                        "detail": "Search engines and browser tabs show a blank title."})
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not (meta_desc.get("content") or "").strip():
        issues.append({"severity": "low", "title": "No meta description",
                        "detail": "Search results show a generic/blank snippet without one."})
    if not soup.find("meta", attrs={"name": "viewport"}):
        issues.append({"severity": "medium", "title": "No mobile viewport tag",
                        "detail": "The page may not render correctly on phones."})
    if not soup.find("link", rel=lambda v: v and "icon" in v.lower()):
        issues.append({"severity": "low", "title": "No favicon",
                        "detail": "The site has no icon for browser tabs/bookmarks."})

    links = list({a.get("href") for a in soup.find_all("a", href=True)
                  if a.get("href", "").startswith(("http://", "https://"))})[:8]
    broken = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=4.0, write=3.0, pool=3.0)) as c:
        for link in links:
            if _is_blocked(link):
                continue
            try:
                lr = await c.head(link, follow_redirects=True)
                if lr.status_code >= 400:
                    broken += 1
            except httpx.RequestError:
                broken += 1
    if broken > 0:
        issues.append({"severity": "medium", "title": f"{broken} broken link(s) found",
                        "detail": f"Checked {len(links)} on-page links; {broken} failed to load."})

    return {"scan_path": "fallback", "title": (title_tag.text or "").strip() if title_tag else "",
            "issues": issues, "response_time_ms": elapsed_ms, "http_status": r.status_code}


@router.post("/onboarding/scan-website")
async def scan_website(body: ScanBody, authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    user_id = user.get("user_id")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    url = _normalize_scan_url(body.url)

    await emit_funnel_event(db, user_id=user_id, event_type="website_scan_started",
                             metadata={"url": url})
    result = await _scan_via_firecrawl(url)
    if result is None:
        result = await _scan_via_fallback(url)
    await emit_funnel_event(db, user_id=user_id, event_type="website_scan_completed",
                             metadata={"url": url, "scan_path": result["scan_path"],
                                       "issue_count": len(result["issues"])})
    logger.info("[onboarding-scan] user=%s url=%s path=%s issues=%d",
                user_id, url, result["scan_path"], len(result["issues"]))

    issues = sorted(result["issues"], key=lambda i: {"high": 0, "medium": 1, "low": 2}.get(i["severity"], 3))
    return {
        "ok": True, "url": url, "scan_path": result["scan_path"],
        "title": result.get("title") or "", "issue_count": len(issues), "issues": issues,
        "summary": (f"Scanned {url} — found {len(issues)} issue(s)." if issues
                    else f"Scanned {url} — no obvious issues found."),
    }



# ── Guided first task — demo project (2026-09-28 "ACTIVATION FIRST" Part 3) ──
# "Try a demo project": a tiny bundled sample (demo_project/sample_app.py)
# with one real, intentional bug. This NEVER touches git and NEVER
# reads/writes the user's own code — it's a fixed, local, safe sandbox
# so users who skip repo-connect can still see ORA actually ship a fix.
_DEMO_FILE = os.path.join(os.path.dirname(__file__), "..", "demo_project", "sample_app.py")
_DEMO_TITLE = "Fix the coupon-discount bug in a small order-total calculator"
_DEMO_EXPECTED = 50.0  # correct total for the $50/10%-off/$5-shipping example below


def _read_demo_source() -> str:
    with open(_DEMO_FILE, "r") as f:
        return f.read()


@router.get("/onboarding/demo-project")
async def demo_project_info():
    return {
        "ok": True,
        "title": _DEMO_TITLE,
        "description": (
            "This tiny order-total function has a real bug: a \"10% off\" "
            "coupon should only take 10% off, but the math treats 10 as "
            "1000% instead — so totals go deeply negative."
        ),
        "source": _read_demo_source(),
    }


async def _run_demo_source(source: str) -> Optional[float]:
    """Executes candidate source in a throwaway subprocess (isolated,
    5s timeout) and returns calculate_order_total(50, 10, 5), or None
    if it errored/timed out. Never touches the real bundled file."""
    import asyncio
    import tempfile
    harness = source + (
        "\n\nif __name__ == '__main__':\n"
        "    print(calculate_order_total(subtotal=50, discount_percent=10, shipping=5))\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tf.write(harness)
        path = tf.name
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        if proc.returncode != 0:
            logger.info("[demo-project] candidate run failed: %s", err.decode()[:300])
            return None
        lines = [ln for ln in out.decode().strip().splitlines() if ln.strip()]
        return float(lines[-1]) if lines else None
    except (ValueError, OSError) as e:
        logger.info("[demo-project] candidate run error: %r", e)
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@router.post("/onboarding/demo-project/run")
async def run_demo_project(authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    user_id = user.get("user_id")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    original = _read_demo_source()
    await emit_funnel_event(db, user_id=user_id, event_type="demo_project_started", metadata={})

    fixed_source = None
    explanation = "The discount math was fixed so a 10%-off coupon takes exactly 10% off, not 1000%."
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        import uuid as _uuid
        api_key = os.environ["EMERGENT_LLM_KEY"]
        chat = (
            LlmChat(
                api_key=api_key,
                session_id=f"demo-project-{_uuid.uuid4().hex[:12]}",
                system_message=(
                    "You are a careful code-fixing assistant. You will be given one "
                    "small Python file with exactly one real bug. Respond with ONLY "
                    "valid JSON: {\"explanation\": \"<one plain-English sentence for a "
                    "non-technical founder>\", \"fixed_source\": \"<the FULL corrected "
                    "file content>\"}. Keep the function signature and file structure "
                    "identical — only fix the actual bug."
                ),
            ).with_model("anthropic", "claude-sonnet-4-6")
        )
        raw = await chat.send_message(UserMessage(text=original))
        text = raw if isinstance(raw, str) else getattr(raw, "text", str(raw))
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        import json as _json
        parsed = _json.loads(text)
        fixed_source = parsed.get("fixed_source") or None
        explanation = parsed.get("explanation") or explanation
    except Exception as e:
        logger.warning("[demo-project] LLM fix failed, using known-good fallback fix: %r", e)

    verified = False
    if fixed_source:
        result_value = await _run_demo_source(fixed_source)
        verified = result_value is not None and abs(result_value - _DEMO_EXPECTED) < 0.01
    if not fixed_source or not verified:
        # LLM unavailable/wrong — fall back to the one-line known-good fix
        # so the demo still ends in a real, verified success either way.
        fixed_source = original.replace(
            "discount = subtotal * discount_percent  # BUG: should divide by 100",
            "discount = subtotal * (discount_percent / 100)",
        )
        result_value = await _run_demo_source(fixed_source)
        verified = result_value is not None and abs(result_value - _DEMO_EXPECTED) < 0.01

    import difflib
    diff = "\n".join(difflib.unified_diff(
        original.splitlines(), fixed_source.splitlines(),
        fromfile="before/sample_app.py", tofile="after/sample_app.py", lineterm="",
    ))

    await emit_funnel_event(db, user_id=user_id, event_type="demo_project_completed",
                             metadata={"verified": verified})
    logger.info("[demo-project] user=%s verified=%s", user_id, verified)

    return {
        "ok": True,
        "verified": verified,
        "title": _DEMO_TITLE,
        "explanation": explanation,
        "diff": diff,
        "before_result": -445.0,
        "after_result": _DEMO_EXPECTED if verified else None,
        "summary": (
            "Fixed and verified — the coupon now takes exactly 10% off."
            if verified else
            "ORA proposed a fix, but automatic verification didn't pass this time."
        ),
    }
