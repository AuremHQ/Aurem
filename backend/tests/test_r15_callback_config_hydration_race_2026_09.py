"""
tests/test_r15_callback_config_hydration_race_2026_09.py — R15

Founder-reported repro (2nd screen recording, tested live on
auremcto.com production): a FRESH install (state present, GitHub
consent screen shown normally, "Install & Authorize" clicked) still
failed with `err=github_probe_failed` right after the GitHub redirect
landed back on `/callback`.

Root cause: `/install` was hardened on 2026-08-24 against the exact
cold-start race where a fresh worker accepts traffic before
`services.github_app_config`'s in-process runtime cache has hydrated
from Mongo — `/install` has a one-shot `ensure_configured_from_db`
fallback for this. `/callback` never got the same guard. Since the
whole OAuth round-trip (user reviewing GitHub's consent screen) can
easily outlast a fresh worker's boot window, `/callback` can land on a
worker whose cache is still empty: `_ga.app_jwt()` (used inside
`_fetch_installation_meta`) raises `GitHubAppNotConfigured`, which the
callback's broad `except Exception` catches and reports as
`err=github_probe_failed` — a generic, misleading "GitHub couldn't
verify the install" message that has nothing to do with the state
token at all.

Fix: `/callback` now calls the same `ensure_configured_from_db`
one-shot fallback `/install` already had, before doing anything that
needs App-JWT signing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from services.github_app_config import set_runtime_github_app_config
from tests.test_github_app_router import (
    fake_db as _fake_db_fixture,
    rsa_pem as _rsa_pem_fixture,
    client as _client_fixture,
    _make_mock_client,
)

fake_db = _fake_db_fixture
rsa_pem = _rsa_pem_fixture
client = _client_fixture


def _github_handler(iid=5001, account_login="tejinder"):
    def handler(request):
        path = request.url.path
        if path == f"/app/installations/{iid}":
            return httpx.Response(200, json={
                "id": iid,
                "account": {"id": 88, "login": account_login, "type": "User"},
                "target_type": "User",
                "repository_selection": "all",
                "permissions": {"metadata": "read"},
                "events": [],
            })
        if path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": []})
        return httpx.Response(404, json={"message": "not mocked"})
    return handler


def _seed_state(fake_db, user_id="user-a", state="gha:user-a:freshtoken"):
    fake_db.oauth_states.rows.append({
        "state":       state,
        "kind":        "github_app_install",
        "user_id":     user_id,
        "used":        False,
        "created_at":  datetime.now(timezone.utc),
        "expires_at":  datetime.now(timezone.utc).timestamp() + 900,
    })
    return state


@pytest.fixture(autouse=True)
def _reset_runtime_config():
    """Every test in this file starts from a clean, empty runtime
    cache — the whole point is testing what happens when it's empty."""
    set_runtime_github_app_config(None)
    yield
    set_runtime_github_app_config(None)


class TestCallbackConfigHydrationRace:
    def test_callback_self_hydrates_from_db_when_cache_empty(
        self, rsa_pem, client, fake_db,
    ):
        """Runtime cache starts EMPTY (simulates the cold-start race)
        but Mongo already has a valid config doc — exactly what
        production looks like moments after a fresh worker boots.
        `/callback` must self-heal instead of misreporting
        github_probe_failed."""
        fake_db.admin_settings = type(fake_db.github_installations)()
        fake_db.admin_settings.rows.append({
            "_id":            "github_app_config",
            "app_id":         "999",
            "app_slug":       "aurem-devops",
            "private_key":    rsa_pem,
            "webhook_secret": "test-secret",
        })
        state = _seed_state(fake_db)
        with patch.object(httpx, "AsyncClient",
                          _make_mock_client(_github_handler(iid=5001))):
            r = client.get(
                f"/api/aurem-dev/github/app/callback"
                f"?installation_id=5001&state={state}",
                follow_redirects=False,
            )
        assert r.status_code == 302
        assert "status=success" in r.headers["location"]
        row = next(x for x in fake_db.github_installations.rows
                   if x["installation_id"] == 5001)
        assert row["user_id"] == "user-a"

    def test_callback_still_soft_fails_cleanly_when_truly_unconfigured(
        self, client, fake_db,
    ):
        """No config anywhere (cache empty AND no admin_settings doc
        at all) — must still soft-fail via the existing
        github_probe_failed path (not crash with a 500)."""
        fake_db.admin_settings = type(fake_db.github_installations)()
        state = _seed_state(fake_db, user_id="user-b", state="gha:user-b:notoken")
        r = client.get(
            f"/api/aurem-dev/github/app/callback"
            f"?installation_id=6002&state={state}",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "err=github_probe_failed" in r.headers["location"]
        assert all(x["installation_id"] != 6002
                   for x in fake_db.github_installations.rows)
