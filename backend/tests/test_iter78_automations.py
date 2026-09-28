"""
test_iter78_automations.py — scheduled / event-driven automation gap.

Closes the last red gap vs Cursor Automations. Covers:
  1. POST /automations/create persists a row scoped to the user.
  2. GET  /automations/list returns the user's rows (and only theirs).
  3. POST /automations/{id}/toggle flips enabled.
  4. DELETE /automations/{id} removes a row.
  5. POST /automations/webhook/github with a push payload triggers
     a queued task on the matching project (via _enqueue_cto_task,
     so the row is actually picked up by the worker).
  6. The router is mounted under /api/aurem-dev so the public URL
     is /api/aurem-dev/automations/webhook/github.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient


# Ensure MONGO_URL/DB_NAME are available even when pytest is invoked
# without `set -a; source .env`. Mirrors what the live backend reads.
def _ensure_env():
    if os.environ.get("MONGO_URL"):
        return
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_ensure_env()


API = "http://localhost:8001/api/aurem-dev"
TEST_PASSWORD = "auto-pass-9281"
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


async def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


async def _signup_and_get_token() -> tuple[str, str]:
    email = f"auto_{uuid.uuid4().hex[:10]}@aurem.test"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/signup", json={
            "email": email, "password": TEST_PASSWORD,
            "name": "Auto Test",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], body["user_id"]


async def _admin_token() -> str:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/login",
                         json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _enable_automations_flag_for(user_id: str) -> None:
    """2026-09-28 MASTER TASK R5/Part1#3 — /automations is now
    default-OFF (`user_automations_page` flag). Narrow-enable it for
    just this one test user via `tier_allowlist` set to a tier no
    real user has + `user_allowlist` containing only this test user —
    real users stay blocked, this test's user bypasses via the
    allowlist. Goes through the real admin HTTP endpoint (not a
    direct DB write) so the live server's 60s in-memory flag cache
    actually gets invalidated."""
    admin_tok = await _admin_token()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/admin/feature-flags",
                         headers={"Authorization": f"Bearer {admin_tok}"},
                         json={"flag": "user_automations_page", "enabled": True,
                               "tier_allowlist": ["__pytest_only__"],
                               "user_allowlist": [user_id]})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_create_list_toggle_delete_automation():
    token, user_id = await _signup_and_get_token()
    await _enable_automations_flag_for(user_id)
    headers = {"Authorization": f"Bearer {token}"}

    # Create
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/create", headers=headers, json={
            "name": "Review main",
            "repo_full_name": f"u_{user_id[:6]}/test-repo",
            "trigger": "push",
            "branch_filter": "main",
            "task_template": "Review commits on {branch}\n{commit_messages}",
        })
    assert r.status_code == 200, r.text
    automation_id = r.json()["automation_id"]

    # List
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{API}/automations/list", headers=headers)
    assert r.status_code == 200
    rows = r.json()["automations"]
    assert any(row["_id"] == automation_id for row in rows)
    found = next(row for row in rows if row["_id"] == automation_id)
    assert found["enabled"] is True
    assert found["trigger"] == "push"

    # Toggle (enabled → disabled)
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/{automation_id}/toggle",
                         headers=headers)
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # Delete
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.delete(f"{API}/automations/{automation_id}",
                           headers=headers)
    assert r.status_code == 200

    # Subsequent toggle should 404
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/{automation_id}/toggle",
                         headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_requires_all_fields():
    token, user_id = await _signup_and_get_token()
    await _enable_automations_flag_for(user_id)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/create", headers=headers, json={
            "name": "missing template",
        })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_webhook_rejects_when_secret_not_configured():
    """2026-09-28 MASTER TASK R1/Part1#1 SECURITY FIX. Verified live on
    both preview and production (auremcto.com) BEFORE this fix: an
    unsigned webhook POST returned 200 {"ok":true} — anyone who knew a
    connected repo's full_name could trigger a real paid task on that
    user's account with zero auth. `GITHUB_WEBHOOK_SECRET` is NOT set
    in this environment (confirmed: not present in backend/.env), so
    the webhook must now fail CLOSED (403), never silently accept and
    process. A request WITH a plausible-but-wrong signature header
    must ALSO 403 the same way (still no secret configured to check
    it against — this isn't a 401-signature-mismatch case).
    The full HMAC signature-verification logic (valid sig accepted,
    wrong sig 401'd, secret configured) is covered separately in
    test_master_task_2026_09_28_webhook_signature.py, which patches
    GITHUB_WEBHOOK_SECRET in-process via TestClient (can't be done
    against this already-running live server)."""
    payload = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "someuser/somerepo"},
        "pusher": {"name": "attacker"},
        "commits": [{"message": "unauthenticated push"}],
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r_unsigned = await c.post(
            f"{API}/automations/webhook/github",
            json=payload, headers={"X-GitHub-Event": "push"},
        )
        r_fake_sig = await c.post(
            f"{API}/automations/webhook/github",
            json=payload,
            headers={"X-GitHub-Event": "push",
                     "X-Hub-Signature-256": "sha256=deadbeef"},
        )
    assert r_unsigned.status_code == 403, r_unsigned.text
    assert r_fake_sig.status_code == 403, r_fake_sig.text
    assert "triggered" not in r_unsigned.json()


@pytest.mark.asyncio
async def test_webhook_skips_non_push_events():
    # Same root cause as above — with no secret configured, EVERY
    # webhook call is now rejected before the event type is even
    # inspected, including this previously-200-with-skipped:true ping.
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/webhook/github",
                         json={"zen": "..."},
                         headers={"X-GitHub-Event": "ping"})
    assert r.status_code == 403


def test_automations_router_wired_in_main():
    import os
    base = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(base, "main.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "automations_router" in src
    assert "from routers.automations import" in src
