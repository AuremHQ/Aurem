"""
tests/test_r14_github_state_dropped_reconnect_2026_09.py — R14

Founder-reported repro (screen recording, 2026-09-04): a user who
ALREADY has the "AUREM DevOps" GitHub App installed re-opens the
connect flow. GitHub's own documented behavior for this exact case is
to route the browser through its "manage installation" screen instead
of a fresh consent screen, and to DROP the `state` query param on the
eventual redirect back to us (only `installation_id` + `setup_action`
survive). The old `/callback` handler only attempted state-recovery
`if state and state_row is None` — a completely ABSENT `state` skipped
that block entirely, silently landing an unlinked/orphaned install
that never shows up for the user (or, on older code, hard-failing).

Two-part fix under test here:
  1. `/callback` — when no usable state (row missing OR state absent)
     exists, fall back to the ALREADY-LINKED user_id on that exact
     `installation_id`, if one exists, before declaring the install
     orphaned.
  2. `POST /claim` — a JWT-authenticated, client-side safety net for
     the narrower case where the installation has genuinely NEVER
     been linked before (e.g. a webhook landed first). The browser
     tab driving the connect flow is authenticated and knows the
     installation_id GitHub handed back — it can claim the row
     directly instead of depending on the state round-trip at all.

Reuses the same FakeDB / TestClient fixtures as test_github_app_router.py.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from tests.test_github_app_router import (
    fake_db as _fake_db_fixture,
    rsa_pem as _rsa_pem_fixture,
    configured as _configured_fixture,
    client as _client_fixture,
    _make_mock_client,
)

# Re-exposed under the exact same names pytest expects — these are the
# SAME already-`@pytest.fixture`-decorated objects from
# test_github_app_router.py, just given a local binding so this file's
# test methods can request them by parameter name.
fake_db = _fake_db_fixture
rsa_pem = _rsa_pem_fixture
configured = _configured_fixture
client = _client_fixture


def _github_handler(iid=9001, account_login="tejinder", repos=None):
    def handler(request):
        path = request.url.path
        if path == f"/app/installations/{iid}":
            return httpx.Response(200, json={
                "id": iid,
                "account": {"id": 77, "login": account_login, "type": "User"},
                "target_type": "User",
                "repository_selection": "all",
                "permissions": {"contents": "write", "metadata": "read"},
                "events": ["installation"],
            })
        if path == "/installation/repositories":
            return httpx.Response(200, json={
                "repositories": (repos or [
                    {"id": 555, "full_name": f"{account_login}/RrootsBeauty",
                     "private": False, "default_branch": "main"},
                ]),
            })
        return httpx.Response(404, json={"message": "not mocked"})
    return handler


class TestCallbackStateDroppedReconnect:
    def test_state_absent_reuses_existing_link(self, configured, client, fake_db):
        """Installation already linked to user-a from a prior connect.
        GitHub drops `state` on this reconnect (documented existing-
        installation behavior) — callback must self-heal by reusing
        the existing link instead of orphaning/erroring."""
        fake_db.github_installations.rows.append({
            "installation_id": 9001,
            "user_id":         "user-a",
            "active":          True,
            "installed_at":    time.time() - 3600,
            "linked_at":       time.time() - 3600,
            "github_login":    "tejinder",
        })
        with patch.object(httpx, "AsyncClient",
                          _make_mock_client(_github_handler(iid=9001))):
            r = client.get(
                "/api/aurem-dev/github/app/callback"
                "?installation_id=9001&setup_action=update",
                follow_redirects=False,
            )
        assert r.status_code == 302
        assert "status=success" in r.headers["location"]
        assert "install_id=9001" in r.headers["location"]

        row = next(x for x in fake_db.github_installations.rows
                   if x["installation_id"] == 9001)
        assert row["user_id"] == "user-a"        # unchanged, not dropped
        assert row["active"] is True

    def test_state_absent_and_never_linked_stays_orphan_but_now_persisted(
        self, configured, client, fake_db,
    ):
        """No prior link exists at all (webhook never landed, no row).
        Callback still soft-fails to `err=invalid_state` — no auto-link,
        no false success — but (2026-09-16, founder-reported live
        incident: an install genuinely succeeded on GitHub's side yet
        was 100% invisible to Aurem, with no admin recourse) a ghost
        row is now persisted (`user_id=None`) so
        `GET /admin/github/installations/unlinked` can find + link it
        later, instead of the fetched account/repo data being
        discarded."""
        with patch.object(httpx, "AsyncClient",
                          _make_mock_client(_github_handler(iid=9002))):
            r = client.get(
                "/api/aurem-dev/github/app/callback"
                "?installation_id=9002&setup_action=update",
                follow_redirects=False,
            )
        assert r.status_code == 302
        assert "err=invalid_state" in r.headers["location"]
        row = next((x for x in fake_db.github_installations.rows
                    if x["installation_id"] == 9002), None)
        assert row is not None
        assert row.get("user_id") is None
        assert row["github_login"] == "tejinder"

    def test_state_present_but_row_missing_still_recovers_via_string(
        self, configured, client, fake_db,
    ):
        """Regression guard: the pre-existing string-based recovery
        (state present, DB row expired/missing) must still work
        exactly as before — this fix only ADDS a new fallback, it
        must not remove the old one."""
        with patch.object(httpx, "AsyncClient",
                          _make_mock_client(_github_handler(iid=9003))):
            r = client.get(
                "/api/aurem-dev/github/app/callback"
                "?installation_id=9003&state=gha:user-b:randomtoken",
                follow_redirects=False,
            )
        assert r.status_code == 302
        assert "status=success" in r.headers["location"]
        row = next(x for x in fake_db.github_installations.rows
                   if x["installation_id"] == 9003)
        assert row["user_id"] == "user-b"


class TestClaimEndpoint:
    def test_claim_requires_auth(self, configured, client):
        r = client.post("/api/aurem-dev/github/app/claim",
                        json={"installation_id": 1})
        assert r.status_code == 401

    def test_claim_404_when_row_missing(self, configured, client):
        r = client.post(
            "/api/aurem-dev/github/app/claim",
            json={"installation_id": 42424242},
            headers={"Authorization": "Bearer user-a"},
        )
        assert r.status_code == 404

    def test_claim_links_orphaned_row(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 7001,
            "user_id":         None,
            "active":          True,
            "installed_at":    time.time() - 10,
        })
        r = client.post(
            "/api/aurem-dev/github/app/claim",
            json={"installation_id": 7001},
            headers={"Authorization": "Bearer user-a"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["already_linked"] is False
        row = next(x for x in fake_db.github_installations.rows
                   if x["installation_id"] == 7001)
        assert row["user_id"] == "user-a"

    def test_claim_noop_when_already_linked_to_caller(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 7002,
            "user_id":         "user-a",
            "active":          True,
            "installed_at":    time.time() - 10,
        })
        r = client.post(
            "/api/aurem-dev/github/app/claim",
            json={"installation_id": 7002},
            headers={"Authorization": "Bearer user-a"},
        )
        assert r.status_code == 200
        assert r.json()["already_linked"] is True

    def test_claim_rejects_hijack_of_another_users_installation(
        self, configured, client, fake_db,
    ):
        fake_db.github_installations.rows.append({
            "installation_id": 7003,
            "user_id":         "user-b",
            "active":          True,
            "installed_at":    time.time() - 10,
        })
        r = client.post(
            "/api/aurem-dev/github/app/claim",
            json={"installation_id": 7003},
            headers={"Authorization": "Bearer user-a"},
        )
        assert r.status_code == 403
        row = next(x for x in fake_db.github_installations.rows
                   if x["installation_id"] == 7003)
        assert row["user_id"] == "user-b"        # untouched

    def test_claim_rejects_stale_orphan_past_window(self, configured, client, fake_db):
        fake_db.github_installations.rows.append({
            "installation_id": 7004,
            "user_id":         None,
            "active":          True,
            "installed_at":    time.time() - (20 * 60),  # 20 min ago > 15 min window
        })
        r = client.post(
            "/api/aurem-dev/github/app/claim",
            json={"installation_id": 7004},
            headers={"Authorization": "Bearer user-a"},
        )
        assert r.status_code == 409
