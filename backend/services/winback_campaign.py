"""
services/winback_campaign.py — Win-back / re-engagement list builder
(2026-09-22 · Revenue Recovery, founder-approved with hard constraints)

Founder's explicit constraints (verbatim, non-negotiable):
  1. Batches kept small — 50 to 150 recipients/day for warm-up.
  2. A FULL preview (subject/body/recipient count + CASL-excluded
     count) must be shown before any send.
  3. No bulk send without the founder's explicit go-ahead EACH TIME —
     no cron, no auto-trigger. `send_batch()` is only ever called from
     an admin-authenticated POST, and even then defaults to
     `dry_run=True` so a send requires BOTH `confirm=True` AND
     `dry_run=False` explicitly set by the caller on every single call.

Segment definition (win-back != the existing 24-72h funnel_stage_nudge
— that cron already handles FRESH stuck users): a real (non-synthetic),
non-internal user stuck in any funnel stage for `min_stuck_days`+
(default 30) — the colder, longer-dormant tail. CASL-screened before
anyone is ever considered send-ready; CASL-excluded contacts are
never silently dropped, always shown with a reason.

State collection: winback_campaign_state
  { user_id, email, sent_at, sent_ok, error, dry_run, message_id }
One row per user_id ever — a user is never sent the win-back message
twice (checked BEFORE any send, same claim-then-send shape as
services/funnel_nudge_cron.py's `_claim_send_slot`).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("aurem.winback_campaign")

CAMPAIGN = "winback"
MIN_BATCH_SIZE = 50
MAX_BATCH_SIZE = 150
DEFAULT_MIN_STUCK_DAYS = 30
PUBLIC_BASE = os.environ.get("PUBLIC_APP_URL", "https://auremcto.com").rstrip("/")
SIGNOFF = "— Tejinder Sandhu, Founder, Aurem"
SUBJECT = "Still stuck on connecting your repo? Let's fix that in 2 minutes"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clamp_batch_size(n: Optional[int]) -> int:
    if n is None:
        return MAX_BATCH_SIZE // 2  # 75, a sane mid-warm-up default
    return max(MIN_BATCH_SIZE, min(MAX_BATCH_SIZE, int(n)))


async def build_candidate_segment(db, *, min_stuck_days: int = DEFAULT_MIN_STUCK_DAYS) -> list[dict]:
    """The colder, longer-dormant tail — same synthetic-filtered stuck
    buckets funnel_nudge_cron already classifies, restricted to users
    stuck for at least `min_stuck_days` (default 30, well past the
    24-72h window the regular nudge cron already covers)."""
    from services.funnel_nudge_cron import classify_users
    buckets = await classify_users(db)
    now_epoch = _now().timestamp()
    min_age_s = min_stuck_days * 86400
    out: list[dict] = []
    for stage, users in buckets.items():
        for u in users:
            if (now_epoch - (u.get("_stuck_since") or 0)) >= min_age_s:
                out.append(u)
    out.sort(key=lambda u: u.get("_stuck_since") or 0)
    return out


async def _already_sent(db, user_id: str) -> bool:
    doc = await db.winback_campaign_state.find_one(
        {"user_id": user_id, "campaign": CAMPAIGN}, {"_id": 1},
    )
    return doc is not None


async def _is_unsubscribed(db, email: str) -> bool:
    from services.first50_campaign import is_unsubscribed
    return await is_unsubscribed(db, email)


def render_text(user: dict) -> str:
    from services.first50_campaign import unsub_url
    first = (user.get("name") or (user.get("email") or "").split("@")[0] or "there").split()[0]
    email = user.get("email") or ""
    return (
        f"Hey {first},\n\n"
        "It's been a while since you signed up for ORA and I noticed you "
        "never got a repo connected. If something got in the way — the "
        "GitHub step, a question about pricing, anything — reply and tell "
        "me, real founder inbox.\n\n"
        f"Or just pick back up here: {PUBLIC_BASE}/dashboard\n\n"
        f"{SIGNOFF}\n\n"
        f"---\nDon't want these emails? Unsubscribe: {unsub_url(email)}\n"
    )


def render_html(user: dict) -> str:
    from services.first50_campaign import unsub_url
    first = (user.get("name") or (user.get("email") or "").split("@")[0] or "there").split()[0]
    email = user.get("email") or ""
    return (
        f'<div style="font-family:-apple-system,sans-serif;max-width:560px;'
        f'margin:0 auto;padding:32px 24px;color:#111">'
        f'<p>Hey {first},</p>'
        f'<p>It\'s been a while since you signed up for ORA and I noticed '
        f'you never got a repo connected. If something got in the way — '
        f'the GitHub step, a question about pricing, anything — reply and '
        f'tell me, real founder inbox.</p>'
        f'<p style="margin:24px 0"><a href="{PUBLIC_BASE}/dashboard" '
        f'style="background:#eab308;color:#000;padding:12px 24px;'
        f'text-decoration:none;border-radius:8px;font-weight:600;'
        f'display:inline-block">Pick back up &rarr;</a></p>'
        f'<p>{SIGNOFF}</p>'
        f'<p style="color:#666;font-size:11px;margin-top:32px;'
        f'padding-top:16px;border-top:1px solid #eee">'
        f'<a href="{unsub_url(email)}" style="color:#666">Unsubscribe</a>'
        f'</p></div>'
    )


async def preview(db, *, min_stuck_days: int = DEFAULT_MIN_STUCK_DAYS,
                   batch_size: Optional[int] = None) -> dict:
    """Read-only. Never sends, never writes. Shows exactly what a send
    call would do: candidate count, CASL eligible/excluded, already-
    sent/unsubscribed counts, the final send-ready count clamped to
    batch_size, and a rendered sample email."""
    from services.casl_consent import casl_screen
    candidates = await build_candidate_segment(db, min_stuck_days=min_stuck_days)
    casl_result = await casl_screen(db, candidates)
    eligible_by_id = {r["user_id"]: r for r in casl_result["eligible"]}

    already_sent = 0
    unsubscribed = 0
    send_ready: list[dict] = []
    for u in candidates:
        uid = u.get("user_id")
        if uid not in eligible_by_id:
            continue
        if await _already_sent(db, uid):
            already_sent += 1
            continue
        if await _is_unsubscribed(db, u.get("email", "")):
            unsubscribed += 1
            continue
        send_ready.append(u)

    clamped = clamp_batch_size(batch_size)
    sample = send_ready[0] if send_ready else {
        "user_id": "preview", "email": "preview@example.com", "name": "Preview User",
    }
    return {
        "candidate_segment_size":   len(candidates),
        "casl_eligible_count":      casl_result["eligible_count"],
        "casl_excluded_count":      casl_result["excluded_count"],
        "casl_excluded_reasons":    {
            r["reason"]: sum(1 for x in casl_result["excluded"] if x["reason"] == r["reason"])
            for r in casl_result["excluded"]
        },
        "already_sent_count":       already_sent,
        "unsubscribed_count":       unsubscribed,
        "send_ready_count":         len(send_ready),
        "requested_batch_size":     clamped,
        "next_batch_recipient_count": min(len(send_ready), clamped),
        "min_stuck_days":           min_stuck_days,
        "sample_email": {
            "recipient": sample.get("email"),
            "subject":   SUBJECT,
            "text":      render_text(sample),
            "html":      render_html(sample),
            "is_placeholder": not bool(send_ready),
        },
        "generated_at": _now().isoformat(),
    }


async def _resend_send(to_email: str, *, subject: str, text: str, html: str) -> tuple[bool, Optional[str]]:
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        return False, "RESEND_API_KEY not configured"
    sender = os.environ.get("RESEND_FROM_EMAIL") or "AUREM <ora@aurem.live>"
    try:
        from services.http import ext_request, ExternalCallError
        try:
            r = await ext_request(
                "resend", "POST", "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"from": sender, "to": [to_email], "subject": subject,
                      "text": text, "html": html},
                raise_for_status=False,
            )
            if r.status_code in (200, 201, 202):
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except ExternalCallError as e:
            return False, f"{e.dep}: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def send_batch(db, *, confirm: bool, dry_run: bool = True,
                      min_stuck_days: int = DEFAULT_MIN_STUCK_DAYS,
                      batch_size: Optional[int] = None) -> dict:
    """Founder-triggered ONLY (admin-authenticated route calls this,
    no cron ever calls this). Requires `confirm=True` AND
    `dry_run=False` explicitly for a real send — either omitted means
    nothing goes out, matching "no bulk send without explicit
    go-ahead each time." Every recipient is claimed (winback_campaign_
    state, one row per user_id) BEFORE sending so a retry can never
    double-send."""
    if not confirm:
        return {"ok": False, "reason": "confirm_required — no send attempted"}

    preview_data = await preview(db, min_stuck_days=min_stuck_days, batch_size=batch_size)
    clamped = preview_data["requested_batch_size"]

    from services.casl_consent import casl_screen
    candidates = await build_candidate_segment(db, min_stuck_days=min_stuck_days)
    casl_result = await casl_screen(db, candidates)
    eligible_ids = {r["user_id"] for r in casl_result["eligible"]}

    to_send: list[dict] = []
    for u in candidates:
        if len(to_send) >= clamped:
            break
        uid = u.get("user_id")
        if uid not in eligible_ids:
            continue
        if await _already_sent(db, uid):
            continue
        if await _is_unsubscribed(db, u.get("email", "")):
            continue
        to_send.append(u)

    sent, failed, recipients = 0, 0, []
    for u in to_send:
        uid, email = u.get("user_id"), u.get("email")
        text = render_text(u)
        html = render_html(u)
        if dry_run:
            recipients.append({"user_id": uid, "email": email, "dry_run": True, "ok": True})
            continue
        # Claim BEFORE sending — mirrors funnel_nudge_cron's atomic-claim
        # pattern so a concurrent/retried call can never double-send.
        try:
            claim = await db.winback_campaign_state.insert_one({
                "user_id": uid, "email": email, "campaign": CAMPAIGN,
                "sent_at": _now(), "sent_ok": False,
                "error": "claimed, send in progress", "dry_run": False,
            })
        except Exception:
            continue  # unique index collision — already claimed elsewhere
        ok, err = await _resend_send(email, subject=SUBJECT, text=text, html=html)
        await db.winback_campaign_state.update_one(
            {"_id": claim.inserted_id},
            {"$set": {"sent_ok": bool(ok), "error": err, "sent_at": _now()}},
        )
        recipients.append({"user_id": uid, "email": email, "dry_run": False, "ok": bool(ok), "error": err})
        if ok:
            sent += 1
        else:
            failed += 1

    return {
        "ok": True, "dry_run": dry_run,
        "attempted": len(to_send), "sent": sent, "failed": failed,
        "recipients": recipients,
        "casl_excluded_count": preview_data["casl_excluded_count"],
        "batch_size_used": clamped,
    }
