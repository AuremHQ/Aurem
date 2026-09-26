"""
services/gate_stuck_nudge.py — fast "stuck at the repo-connect gate" nudge.

2026-09-22 — real activity-log review surfaced a case the existing
`funnel_nudge_cron.py` doesn't cover on a useful timeline: a user who
started the GitHub connect flow (clicked "Connect GitHub", or actually
reached GitHub — `github_auth_started` / `app_install_redirect` /
`repo_connect_gate_shown`) but, after a few hours, has neither finished
connecting a real repo NOR explicitly clicked "Skip for now". The same
review found `chat_opened` fires the instant ChatPanel mounts — which
happens UNDER the gate overlay too — so these users looked identical
in the activity log to ones who'd actually reached usable chat (fixed
separately in Dashboard.jsx / ChatPanel.jsx: `chat_opened` now waits
for the gate to actually resolve).

This module is deliberately separate from `funnel_nudge_cron.py`
(24h-to-classify-stuck, 72h-before-first-wave, 4 broad lifecycle
stages) — that system is intentionally slow and broad. This one is
one narrow, time-sensitive case with an hours-scale window, sent once.

Deliberately excludes:
  - Users who already connected a repo (`cto_projects` row exists) —
    resolved via Case A.
  - Users who explicitly skipped (`repo_connect_gate_skipped` = True
    on dev_users) — resolved via Case B, an intentional choice.
  - Users who never started the connect flow at all — no signal to
    react to; `funnel_nudge_cron.py`'s `stage4_fully_inactive` covers
    those on its own (much longer) timeline.

Public surface:
    stuck_gate_users(db)                 -> list[dict]
    send_gate_stuck_nudge(user, ...)     -> dict
    run_gate_stuck_nudge_batch(db, ...)  -> dict
    gate_stuck_nudge_cron(interval_s)    -> None (hourly loop)
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

CAMPAIGN = "gate_stuck_nudge"
# "kuch ghanton baad" (a few hours later) — configurable. Deliberately
# far shorter than funnel_nudge_cron's 24-72h broad-stage window: this
# is one narrow case, not the general lifecycle classifier.
GATE_STUCK_HOURS = float(os.environ.get("GATE_STUCK_NUDGE_HOURS", "4"))
MAX_NUDGES_PER_RUN = int(os.environ.get("GATE_STUCK_NUDGE_MAX_PER_RUN", "50"))

# Signals that the user actually started the connect flow (as opposed
# to never having touched it — those are `stage4_fully_inactive`'s
# job, not this one's).
_STARTED_STAGES = (
    "github_auth_started", "connect_repo_click",
    "app_install_redirect", "repo_connect_gate_shown",
)

SUBJECT = "Still there? Finish connecting your repo"
BODY = (
    "You started connecting a GitHub repo but didn't finish — no repo "
    "is linked to your account yet, and it doesn't look like you "
    "skipped that step either.\n\n"
    "Head back to your dashboard and pick up right where you left "
    "off — it only takes 2 minutes. If you'd rather not connect a "
    "repo right now, that's fine too: just click \"Skip for now\" on "
    "the setup screen."
)


def _now() -> datetime:
    return datetime.now(UTC)


def _first_name(user: dict) -> str:
    name = (user.get("name") or "").strip()
    if name:
        return name.split()[0]
    email = (user.get("email") or "").strip()
    return (email.split("@", 1)[0] or "there").split(".", 1)[0]


def _event_ts(row: dict, primary_field: str) -> float:
    ts = row.get(primary_field)
    if isinstance(ts, (int, float)):
        return float(ts)
    ca = row.get("created_at")
    if isinstance(ca, datetime):
        return ca.timestamp()
    return 0.0


async def stuck_gate_users(db) -> list[dict]:
    """Users stuck at the gate: started the connect flow, still no
    project and no explicit skip after GATE_STUCK_HOURS. Batched —
    candidate set comes from dev_users first (bounded), then the two
    funnel-event collections are queried filtered to that set, same
    shape as `funnel_nudge_cron.classify_users()`."""
    from services.synthetic_filter import is_synthetic

    candidates = await db.dev_users.find(
        {"email": {"$exists": True, "$ne": ""}},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1,
         "repo_connect_gate_skipped": 1, "is_admin": 1, "is_unlimited": 1,
         "tier": 1, "first_ship_at": 1},
    ).to_list(length=10_000)
    candidates = [
        u for u in candidates
        if u.get("user_id")
        and not (u.get("is_admin") or u.get("is_unlimited") or u.get("tier") == "founder")
        and not u.get("first_ship_at")
        and not u.get("repo_connect_gate_skipped")
        and not is_synthetic(u.get("email"))
    ]
    ids = [u["user_id"] for u in candidates]
    if not ids:
        return []

    with_project = set(await db.cto_projects.distinct("user_id", {"user_id": {"$in": ids}}))
    ids = [uid for uid in ids if uid not in with_project]
    if not ids:
        return []

    first_seen: dict[str, float] = {}
    for coll_name, stage_field, ts_field in (
        ("github_funnel_events", "stage", "ts"),
        ("funnel_events", "event_type", "ts_epoch"),
    ):
        try:
            coll = getattr(db, coll_name)
            cur = coll.find(
                {stage_field: {"$in": list(_STARTED_STAGES)}, "user_id": {"$in": ids}},
                {"_id": 0, "user_id": 1, ts_field: 1, "created_at": 1},
            )
            async for row in cur:
                uid = row.get("user_id")
                if not uid:
                    continue
                ts = _event_ts(row, ts_field)
                if not ts:
                    continue
                if uid not in first_seen or ts < first_seen[uid]:
                    first_seen[uid] = ts
        except Exception as e:
            logger.warning("gate_stuck_nudge: %s scan failed: %r", coll_name, e)

    cutoff = _now().timestamp() - GATE_STUCK_HOURS * 3600
    by_id = {u["user_id"]: u for u in candidates}
    out: list[dict] = []
    for uid, ts in first_seen.items():
        if ts > cutoff:
            continue  # not stuck long enough yet
        u = by_id.get(uid)
        if not u:
            continue
        u = dict(u)
        u["_stuck_since"] = ts
        out.append(u)
    out.sort(key=lambda u: u["_stuck_since"])
    return out


async def _has_been_sent(db, user_id: str) -> bool:
    doc = await db.onboarding_emails.find_one(
        {"user_id": user_id, "campaign": CAMPAIGN}, {"_id": 1},
    )
    return doc is not None


async def _claim_send_slot(db, user_id: str, email: str) -> object | None:
    """Atomic claim BEFORE sending — same pattern (and the same
    `uniq_user_campaign_stage` unique index) as `funnel_nudge_cron.
    _claim_send_slot`, so a double-boot / rolling-deploy race can't
    send this one-shot nudge twice. `stage` is fixed to CAMPAIGN since
    this nudge has only one variant."""
    from pymongo.errors import DuplicateKeyError
    try:
        res = await db.onboarding_emails.insert_one({
            "user_id": user_id, "email": email, "campaign": CAMPAIGN,
            "stage": CAMPAIGN, "sent_at": _now(), "sent_ok": False,
            "error": "claimed, send in progress", "dry_run": False,
            "clicked_at": None, "click_count": 0,
        })
        return res.inserted_id
    except DuplicateKeyError:
        return None


async def _finalize_send(db, doc_id, sent_ok: bool, error: str | None) -> None:
    try:
        await db.onboarding_emails.update_one(
            {"_id": doc_id},
            {"$set": {"sent_ok": bool(sent_ok), "error": error, "sent_at": _now()}},
        )
    except Exception as e:
        logger.warning("gate_stuck_nudge finalize_send failed: %r", e)


async def render_text(db, user: dict, *, preview: bool = False) -> str:
    from services.email_link_tokens import unsub_url
    from services.funnel_nudge_cron import magic_click_url
    first = _first_name(user)
    cta = await magic_click_url(db, user.get("user_id", ""), user.get("email", ""), CAMPAIGN, preview=preview)
    unsub = unsub_url(user.get("email", ""))
    return (
        f"Hey {first},\n\n{BODY}\n\n"
        f"Open your dashboard → {cta}\n\n"
        f"— Tejinder Sandhu, Founder, Aurem\n\n"
        f"---\nDon't want these emails? Unsubscribe: {unsub}\n"
    )


async def render_html(db, user: dict, *, preview: bool = False) -> str:
    from services.email_link_tokens import unsub_url
    from services.funnel_nudge_cron import magic_click_url
    first = _first_name(user)
    body_html = BODY.replace("\n\n", "<br><br>")
    cta = await magic_click_url(db, user.get("user_id", ""), user.get("email", ""), CAMPAIGN, preview=preview)
    unsub = unsub_url(user.get("email", ""))
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0b0b;color:#e8e8e8;
font-family:'Helvetica Neue',Arial,sans-serif;line-height:1.55;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         width="100%" style="background:#0b0b0b;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             width="560" style="max-width:560px;background:#141414;
                                 border:1px solid rgba(255,102,8,0.22);
                                 border-radius:12px;padding:32px;">
        <tr><td style="color:#e8e8e8;font-size:15px;">
          Hey {first},<br><br>
          {body_html}<br><br>
          <a href="{cta}"
             style="display:inline-block;padding:12px 22px;background:#ff6608;
                    color:#0b0b0b;text-decoration:none;font-weight:600;
                    border-radius:8px;font-size:14px;">
            Open your dashboard &rarr;
          </a><br><br>
          <span style="color:#aaa;font-size:13px;">— Tejinder Sandhu, Founder, Aurem</span>
        </td></tr>
      </table>
      <p style="color:#555;font-size:11px;margin-top:16px;">
        <a href="{unsub}" style="color:#555;">Unsubscribe</a> from these emails.
      </p>
    </td></tr>
  </table>
</body></html>"""


async def send_gate_stuck_nudge(user: dict, *, dry_run: bool = False) -> dict:
    from cto_services.db import get_db
    from services.funnel_nudge_cron import _resend_send

    db = get_db()
    if db is None:
        return {"ok": False, "error": "db unavailable", "user_id": user.get("user_id")}
    if dry_run:
        text = await render_text(db, user, preview=True)
        return {"ok": True, "user_id": user.get("user_id"), "email": user.get("email"),
                "dry_run": True, "preview": {"subject": SUBJECT, "text": text}}
    claim_id = await _claim_send_slot(db, user["user_id"], user["email"])
    if claim_id is None:
        return {"ok": True, "user_id": user.get("user_id"), "email": user.get("email"),
                "skipped": True, "reason": "already claimed"}
    text = await render_text(db, user)
    html = await render_html(db, user)
    sent_ok, err = await _resend_send(user["email"], subject=SUBJECT, text=text, html=html)
    await _finalize_send(db, claim_id, sent_ok, err)
    return {"ok": bool(sent_ok), "user_id": user.get("user_id"), "email": user.get("email"), "error": err}


async def run_gate_stuck_nudge_batch(db, *, dry_run: bool = False) -> dict:
    from services.email_link_tokens import is_unsubscribed

    summary = {"ok": True, "dry_run": bool(dry_run), "sent": 0, "skipped": 0,
               "failed": 0, "recipients": [], "errors": [], "deferred_to_next_run": 0}
    cohort = await stuck_gate_users(db)
    eligible = []
    for u in cohort:
        if not dry_run and await _has_been_sent(db, u["user_id"]):
            continue
        if await is_unsubscribed(db, u.get("email", "")):
            continue
        eligible.append(u)
    to_send, deferred = eligible[:MAX_NUDGES_PER_RUN], eligible[MAX_NUDGES_PER_RUN:]
    summary["deferred_to_next_run"] = len(deferred)
    for u in to_send:
        res = await send_gate_stuck_nudge(u, dry_run=dry_run)
        summary["recipients"].append({
            "user_id": res.get("user_id"), "email": res.get("email"),
            "ok": bool(res.get("ok")), "dry_run": bool(dry_run),
            "skipped": bool(res.get("skipped")),
        })
        if res.get("skipped"):
            summary["skipped"] += 1
        elif res.get("ok"):
            summary["sent"] += 1
        else:
            summary["failed"] += 1
            if res.get("error"):
                summary["errors"].append(res["error"])
    return summary


async def gate_stuck_nudge_cron(interval_seconds: int = 3600, *, startup_delay_seconds: int = 180) -> None:
    """Hourly loop by default — much faster cadence than
    `funnel_nudge_cron.nudge_cron`'s daily tick, since this nudge's
    own window is hours, not days. Idempotent via the atomic claim in
    `send_gate_stuck_nudge`, same as the daily cron."""
    import asyncio

    from cto_services.db import get_db

    await asyncio.sleep(startup_delay_seconds)
    while True:
        try:
            db = get_db()
            if db is not None:
                result = await run_gate_stuck_nudge_batch(db, dry_run=False)
                if result["sent"] or result["failed"] or result["skipped"]:
                    logger.info("🪧 gate_stuck_nudge cron — sent=%d failed=%d skipped=%d",
                                result["sent"], result["failed"], result["skipped"])
        except Exception as e:
            logger.warning("gate_stuck_nudge_cron tick failed: %r", e)
        await asyncio.sleep(interval_seconds)
