"""
services/team_invite_email.py — 2026-02 · Phase 1 (Team Roles & Permissions)

Sends the "you've been invited to join the AUREM team" link. Mirrors
the Resend pattern in services/password_reset_email.py (same
provider, same shared HTTP wrapper). Best-effort: a failed send must
never raise into the request path that triggered it — the invite row
still exists and can be resent.
"""
from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)

_SUBJECT = "You've been invited to AUREM"


def _invite_url(token: str) -> str:
    base = os.environ.get("FRONTEND_URL") or "https://auremcto.com"
    return f"{base.rstrip('/')}/team/accept-invite?token={token}"


def _render_text(url: str, owner_email: str, role_label: str) -> str:
    return (
        f"{owner_email} invited you to join their AUREM team as {role_label}.\n\n"
        f"Set up your account: {url}\n\n"
        "This link expires in 7 days."
    )


def _render_html(url: str, owner_email: str, role_label: str) -> str:
    return (
        f"<p>{owner_email} invited you to join their AUREM team as <b>{role_label}</b>.</p>"
        f'<p><a href="{url}">Set up your account</a></p>'
        "<p style=\"color:#888;font-size:12px\">This link expires in 7 days.</p>"
    )


async def send_invite_email(to_email: str, token: str, owner_email: str,
                             role_label: str) -> tuple[bool, str | None]:
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        logger.warning("send_invite_email: RESEND_API_KEY not configured")
        return False, "RESEND_API_KEY not configured"
    sender = os.environ.get("RESEND_FROM_EMAIL") or "AUREM <ora@aurem.live>"
    url = _invite_url(token)
    try:
        from services.http import ext_request, ExternalCallError
        try:
            r = await ext_request(
                "resend", "POST",
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type":   "application/json"},
                json={
                    "from":    sender,
                    "to":      [to_email],
                    "subject": _SUBJECT,
                    "text":    _render_text(url, owner_email, role_label),
                    "html":    _render_html(url, owner_email, role_label),
                },
                raise_for_status=False,
            )
            if r.status_code in (200, 201, 202):
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except ExternalCallError as e:
            return False, f"{e.dep}: {e}"
    except Exception as e:                                    # noqa: BLE001
        logger.warning("send_invite_email failed: %r", e)
        return False, f"{type(e).__name__}: {e}"
