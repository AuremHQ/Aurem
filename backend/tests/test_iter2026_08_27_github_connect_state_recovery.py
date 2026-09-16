"""
tests/test_iter2026_08_27_github_connect_state_recovery.py

2026-08-27 — founder-reported production bug (video evidence): a real
GitHub App install (RevootsBeauty/Revoots) completed cleanly on
GitHub's side, but the AUREM wizard silently reverted to the "Connect
your GitHub repo" CTA with ZERO error/toast — still stuck, no retry
worked (a page refresh re-runs the same broken status check).

Root cause traced via routers/github_app.py::install_callback: the
`oauth_states` row backing the `state` param is single-use with a 15-
minute TTL (`_STATE_TTL_SECONDS`). If it's missing/expired/already-
used by the time `/callback` runs, `user_id_to_link` fell back to None
— the row lands in `github_installations` with user_id=null (via the
async webhook's `installation.created`, which has no user context at
all), and `/github/app/status` (queried by user_id) never finds it.
The wizard has no way to know the install genuinely succeeded.

Fix: our own state string is `gha:<user_id>:<24-byte urlsafe random>`
(routers/github_app.py::install_kickoff). The random suffix is
unforgeable — nobody can produce `gha:<victim_user_id>:<matching
random>` without already having been issued exactly that token. So on
a DB-row miss it is safe to recover `user_id` from the string itself
rather than dropping the link — this directly closes the "silent
stuck" bug independent of *why* the row lookup missed (TTL expiry,
GC, a benign double-fire of the callback, etc).
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if isinstance(v, dict) and "$gt" in v:
                if not (row.get(k, 0) > v["$gt"]):
                    return False
                continue
            if row.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def find_one(self, query=None, projection=None):
        for r in self.rows:
            if self._match(r, query):
                return dict(r)
        return None

    def find(self, query=None, projection=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]

        class _Cur:
            def __init__(self, rows):
                self._rows = rows

            def sort(self, *a, **kw):
                return self

            async def to_list(self, length=None):
                return self._rows

        return _Cur(matched)

    async def find_one_and_update(self, query, update, projection=None,
                                   upsert=False, return_document=None):
        for r in self.rows:
            if self._match(r, query):
                r.update(update.get("$set") or {})
                return dict(r)
        if upsert:
            new_row = dict(query or {})
            new_row.update(update.get("$set") or {})
            new_row.update(update.get("$setOnInsert") or {})
            self.rows.append(new_row)
            return dict(new_row)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if self._match(r, query):
                r.update(update.get("$set") or {})
                return
        if upsert:
            new_row = dict(query or {})
            new_row.update(update.get("$set") or {})
            self.rows.append(new_row)

    async def update_many(self, query, update):
        for r in self.rows:
            if self._match(r, query):
                r.update(update.get("$set") or {})


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


FAKE_META = {
    "account": {"login": "drilltest", "type": "User", "id": 42},
    "target_type": "User",
    "repository_selection": "selected",
    "permissions": {}, "events": [],
}


def _make_client(fake_db):
    from cto_services import db as _dbmod
    _dbmod.set_db(fake_db)
    from routers import github_app as ga_mod
    app = FastAPI()
    app.include_router(ga_mod.router, prefix="/api/aurem-dev")
    return TestClient(app), ga_mod


def test_expired_state_recovers_user_id_from_embedded_string():
    fake_db = _FakeDB()
    client, ga_mod = _make_client(fake_db)
    state = "gha:u_real_founder:abc123unforgeable"
    # No oauth_states row inserted — simulates an expired/GC'd row.
    with patch.object(ga_mod, "_fetch_installation_meta", AsyncMock(return_value=FAKE_META)), \
         patch.object(ga_mod._ga, "list_installation_repos", AsyncMock(return_value=[])), \
         patch.object(ga_mod, "_funnel_track", AsyncMock()):
        r = client.get(
            "/api/aurem-dev/github/app/callback",
            params={"installation_id": 555, "state": state},
            follow_redirects=False,
        )
    assert r.status_code in (302, 307)
    assert "status=success" in r.headers["location"]
    row = fake_db.github_installations.rows[0]
    assert row["user_id"] == "u_real_founder"
    assert row["active"] is True


def test_status_now_finds_the_recovered_installation():
    """End-to-end proof of the actual reported symptom: GET
    /github/app/status for the SAME user now reports installation_active
    after the fix — before the fix this stayed False forever."""
    fake_db = _FakeDB()
    client, ga_mod = _make_client(fake_db)
    state = "gha:u_real_founder2:xyz987unforgeable"
    with patch.object(ga_mod, "_fetch_installation_meta", AsyncMock(return_value=FAKE_META)), \
         patch.object(ga_mod._ga, "list_installation_repos", AsyncMock(return_value=[
             {"id": 1, "full_name": "RevootsBeauty/Revoots",
              "private": False, "default_branch": "main"},
         ])), \
         patch.object(ga_mod, "_funnel_track", AsyncMock()):
        client.get(
            "/api/aurem-dev/github/app/callback",
            params={"installation_id": 556, "state": state},
            follow_redirects=False,
        )

    async def _fake_current_dev(authorization=None):
        return {"user_id": "u_real_founder2"}

    orig = ga_mod.current_dev
    ga_mod.current_dev = _fake_current_dev
    try:
        r = client.get("/api/aurem-dev/github/app/status",
                        headers={"Authorization": "Bearer x"})
    finally:
        ga_mod.current_dev = orig
    assert r.status_code == 200
    data = r.json()
    assert data["installation_active"] is True
    assert data["state"] == "connected"
    assert data["connected_repo"] == "RevootsBeauty/Revoots"


def test_malformed_state_without_gha_prefix_still_fails_closed():
    """Security guard: recovery only trusts OUR OWN unforgeable token
    format (`gha:<user_id>:<random>`) — a string that doesn't match it
    (can't happen from a real /install kickoff) must still fail closed,
    NEVER attribute the install to an arbitrary/forged value.

    2026-09-16 update: the ghost row IS now persisted (see
    test_orphaned_install_logs_account_and_repos_for_grep below for
    why) — the security property under test here is narrower and
    still holds: the persisted row's `user_id` must be None, never
    something derived from the untrusted `state` string."""
    fake_db = _FakeDB()
    client, ga_mod = _make_client(fake_db)
    with patch.object(ga_mod, "_fetch_installation_meta", AsyncMock(return_value=FAKE_META)), \
         patch.object(ga_mod._ga, "list_installation_repos", AsyncMock(return_value=[])):
        r = client.get(
            "/api/aurem-dev/github/app/callback",
            params={"installation_id": 557, "state": "not-our-format"},
            follow_redirects=False,
        )
    assert r.status_code in (302, 307)
    assert "err=invalid_state" in r.headers["location"]
    row = next((x for x in fake_db.github_installations.rows
                if x["installation_id"] == 557), None)
    assert row is not None and row.get("user_id") is None


def test_valid_unexpired_state_row_still_takes_priority_over_recovery():
    """A present + valid state row remains the source of truth — the
    string-recovery fallback only kicks in on a genuine DB-row miss."""
    fake_db = _FakeDB()
    client, ga_mod = _make_client(fake_db)
    state = "gha:u_would_be_recovered:randtok"
    fake_db.oauth_states.rows.append({
        "state": state, "kind": "github_app_install", "used": False,
        "expires_at": time.time() + 600, "user_id": "u_correct_from_row",
        "funnel_session": "sess_real",
    })
    with patch.object(ga_mod, "_fetch_installation_meta", AsyncMock(return_value=FAKE_META)), \
         patch.object(ga_mod._ga, "list_installation_repos", AsyncMock(return_value=[])), \
         patch.object(ga_mod, "_funnel_track", AsyncMock()):
        client.get(
            "/api/aurem-dev/github/app/callback",
            params={"installation_id": 558, "state": state},
            follow_redirects=False,
        )
    row = fake_db.github_installations.rows[0]
    assert row["user_id"] == "u_correct_from_row"


def test_missing_state_param_entirely_now_ghost_row_persisted_admin_linkable(caplog):
    """No `state` query param at all (e.g. a forged direct hit on the
    callback URL, OR — confirmed live 2026-09-04 via founder screen
    recording — GitHub's documented behavior of dropping `state`
    entirely when a user who ALREADY has this App installed revisits
    the connect flow).

    2026-09-04: this used to soft-fail with NO db write at all,
    reasoning that a "poisoned"/ghost row was worse than a clean
    slate for the next attempt.

    2026-09-16 REVERSED, with new evidence (founder-reported live
    incident: rebecca@millingtonstudio.com's GitHub-side install +
    7-repo selection genuinely succeeded, but was 100% invisible to
    Aurem — no admin tool existed to find/fix an unlinked install,
    confirmed by cross-checking the SAME App's real GitHub API data).
    Verified this reversal does NOT reintroduce the 2026-09-04 bug:
    the user-facing redirect is UNCHANGED (still `err=invalid_state`,
    still no auto-link, still no false "success") — a ghost row now
    just sits in `github_installations` (`user_id=None`) so
    `GET /admin/github/installations/unlinked` can find + link it,
    instead of the fetched account/repo data being fetched only to
    be thrown away."""
    fake_db = _FakeDB()
    client, ga_mod = _make_client(fake_db)
    with patch.object(ga_mod, "_fetch_installation_meta", AsyncMock(return_value=FAKE_META)), \
         patch.object(ga_mod._ga, "list_installation_repos", AsyncMock(return_value=[])):
        r = client.get(
            "/api/aurem-dev/github/app/callback",
            params={"installation_id": 559},
            follow_redirects=False,
        )
    assert r.status_code in (302, 307)
    assert "err=invalid_state" in r.headers["location"]
    row = next((x for x in fake_db.github_installations.rows
                if x["installation_id"] == 559), None)
    assert row is not None
    assert row.get("user_id") is None
    assert row["github_login"] == "drilltest"
    assert row["active"] is True


def test_missing_state_but_installation_already_linked_self_heals():
    """The other half of the 2026-09-04 fix: when `state` is dropped
    (existing-installation reconnect) but this installation_id was
    ALREADY linked to a user from a prior successful connect, that's
    not a new link attempt at all — it's GitHub re-confirming an
    existing install. Must self-heal to success, not fail or orphan."""
    fake_db = _FakeDB()
    fake_db.github_installations.rows.append({
        "installation_id": 559,
        "user_id":         "founder-1",
        "active":          True,
        "installed_at":    time.time() - 3600,
    })
    client, ga_mod = _make_client(fake_db)
    with patch.object(ga_mod, "_fetch_installation_meta", AsyncMock(return_value=FAKE_META)), \
         patch.object(ga_mod._ga, "list_installation_repos", AsyncMock(return_value=[])):
        r = client.get(
            "/api/aurem-dev/github/app/callback",
            params={"installation_id": 559, "setup_action": "update"},
            follow_redirects=False,
        )
    assert r.status_code in (302, 307)
    assert "status=success" in r.headers["location"]
    row = next(x for x in fake_db.github_installations.rows
               if x["installation_id"] == 559)
    assert row["user_id"] == "founder-1"


def test_orphaned_install_logs_account_and_repos_for_grep(caplog):
    """2026-08-27 (round 2) — founder asked: if the truly-orphaned case
    (no recoverable user_id) is only fixable via manual revoke+reinstall
    for now, at minimum log installation_id + account + repo so the
    next incident can be grepped instead of guessed at. This is the
    ONLY path left where `/callback` can't self-heal (malformed/forged
    state, or state genuinely absent from a direct-callback hit) —
    every other failure mode recovers via the embedded-string fallback
    above.

    2026-09-16: "manual revoke+reinstall" was never a real fix a
    stuck paying customer would know to do — see the ghost-row-
    persistence reversal above. The log line now also confirms
    whether the persist succeeded, and a ghost row must exist for
    grep to have somewhere useful to point admins (the new
    `/admin/github/installations/unlinked` tool)."""
    fake_db = _FakeDB()
    client, ga_mod = _make_client(fake_db)
    with patch.object(ga_mod, "_fetch_installation_meta", AsyncMock(return_value=FAKE_META)), \
         patch.object(ga_mod._ga, "list_installation_repos", AsyncMock(return_value=[
             {"id": 1, "full_name": "SomeOrg/orphaned-repo"},
         ])):
        import logging
        with caplog.at_level(logging.ERROR, logger="routers.github_app"):
            r = client.get(
                "/api/aurem-dev/github/app/callback",
                params={"installation_id": 560, "state": "not-our-format"},
                follow_redirects=False,
            )
    assert r.status_code in (302, 307)
    assert "err=invalid_state" in r.headers["location"]
    row = next((x for x in fake_db.github_installations.rows
                if x["installation_id"] == 560), None)
    assert row is not None and row.get("user_id") is None  # ghost row, still unlinked
    orphan_logs = [rec.message for rec in caplog.records if "GH_CONNECT_ORPHANED_INSTALL" in rec.message]
    assert len(orphan_logs) == 1
    assert "560" in orphan_logs[0]
    assert "drilltest" in orphan_logs[0]
    assert "SomeOrg/orphaned-repo" in orphan_logs[0]
    assert "persisted" in orphan_logs[0]
