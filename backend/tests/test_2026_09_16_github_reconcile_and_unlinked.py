"""
tests/test_2026_09_16_github_reconcile_and_unlinked.py

Founder-reported live incident (2026-09-16): rebecca@millingtonstudio.com
reported her GitHub App install + 7-repo selection succeeded on GitHub's
side, but Aurem's DB had zero record of it — confirmed by cross-checking
the SAME shared App's real GitHub API data (another known user's
installation existed live on GitHub but was completely absent from our
`github_installations` collection). Root causes + fixes covered here:

  1. `services/github_app.py::upsert_installation` — the persistence
     helper moved here (was router-local `_upsert_installation`) so
     both the webhook/callback handlers AND the new reconciliation
     tooling share ONE upsert path.
  2. `services/github_app.py::reconcile_installations` — poll-based
     fallback for GitHub's push-only webhook: lists every installation
     directly from GitHub (ground truth) and backfills any
     `installation_id` missing from our DB as an unlinked ghost row.
  3. `services/github_app.py::webhook_fence_status(event_filter=...)`
     — lets an admin cut through high-frequency `workflow_job` noise
       to find the real delivery attempt for an `installation` event.
  4. `routers/admin_ops_config.py` — three new admin endpoints:
     GET  /admin/github/installations/unlinked
     POST /admin/github/installations/reconcile
     POST /admin/github/installations/{id}/link
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
import httpx

from services import github_app as ga


# ═════════════════════════════════════════════════════════════════════
# Fakes
# ═════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **kw):
        return self

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeInstallationsCollection:
    def __init__(self):
        self.rows: list[dict] = []

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()
                   if not isinstance(v, dict)):
                if "installation_id" in query and \
                        r.get("installation_id") != query["installation_id"]:
                    continue
                return dict(r)
        return None

    def find(self, query, projection=None):
        def _matches(r):
            if "$or" in query:
                ors = query["$or"]
                ok = False
                for cond in ors:
                    for k, v in cond.items():
                        if isinstance(v, dict) and "$exists" in v:
                            has = k in r
                            if has == v["$exists"]:
                                ok = True
                        elif r.get(k) == v:
                            ok = True
                return ok
            if "installation_id" in query and "$in" in query["installation_id"]:
                return r.get("installation_id") in query["installation_id"]["$in"]
            return True
        return _FakeCursor([dict(r) for r in self.rows if _matches(r)])

    async def find_one_and_update(self, query, update, upsert=False, return_document=None):
        for r in self.rows:
            if r.get("installation_id") == query.get("installation_id"):
                r.update(update.get("$set") or {})
                return dict(r)
        if upsert:
            new_row = dict(query)
            new_row.update(update.get("$set") or {})
            new_row.update(update.get("$setOnInsert") or {})
            self.rows.append(new_row)
            return dict(new_row)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if r.get("installation_id") == query.get("installation_id"):
                r.update(update.get("$set") or {})
                return
        if upsert:
            new_row = dict(query)
            new_row.update(update.get("$set") or {})
            self.rows.append(new_row)


class _FakeSimpleCollection:
    """Generic find_one/insert_one fake for dev_users / admin_audit."""
    def __init__(self, seed=None):
        self.rows: list[dict] = list(seed or [])

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))


class _FakeDB:
    def __init__(self):
        self.github_installations = _FakeInstallationsCollection()
        self.dev_users = _FakeSimpleCollection()
        self.admin_audit = _FakeSimpleCollection()


# ═════════════════════════════════════════════════════════════════════
# 1. upsert_installation
# ═════════════════════════════════════════════════════════════════════

FAKE_INST = {
    "id": 900001,
    "account": {"login": "some-org", "type": "Organization", "id": 55},
    "target_type": "Organization",
    "repository_selection": "all",
    "permissions": {"contents": "write"},
    "events": ["installation"],
    "suspended_at": None,
}


class TestUpsertInstallation:
    @pytest.mark.asyncio
    async def test_ghost_row_has_no_user_id(self):
        db = _FakeDB()
        row = await ga.upsert_installation(db, 900001, FAKE_INST)
        assert row["user_id"] is None if "user_id" in row else "user_id" not in row
        assert row["github_login"] == "some-org"
        assert row["active"] is True

    @pytest.mark.asyncio
    async def test_linked_row_sets_user_id_and_linked_at(self):
        db = _FakeDB()
        row = await ga.upsert_installation(db, 900002, FAKE_INST, user_id="u_real")
        assert row["user_id"] == "u_real"
        assert row["linked_at"] > 0

    @pytest.mark.asyncio
    async def test_idempotent_reupsert_preserves_installed_at(self):
        db = _FakeDB()
        first = await ga.upsert_installation(db, 900003, FAKE_INST)
        installed_at_1 = first["installed_at"]
        second = await ga.upsert_installation(db, 900003, FAKE_INST)
        assert second["installed_at"] == installed_at_1
        assert len(db.github_installations.rows) == 1


# ═════════════════════════════════════════════════════════════════════
# 2. reconcile_installations
# ═════════════════════════════════════════════════════════════════════

class TestReconcileInstallations:
    @pytest.mark.asyncio
    async def test_backfills_missing_installation_as_ghost_row(self):
        db = _FakeDB()
        live = [
            {"id": 161428545, "account": {"login": "canndalf", "type": "User", "id": 1},
             "target_type": "User", "repository_selection": "all",
             "permissions": {}, "events": [], "suspended_at": None},
        ]
        with patch.object(ga, "list_installations", AsyncMock(return_value=live)):
            result = await ga.reconcile_installations(db)
        assert result["ok"] is True
        assert result["checked"] == 1
        assert result["added"] == [161428545]
        row = db.github_installations.rows[0]
        assert row["installation_id"] == 161428545
        assert row["github_login"] == "canndalf"
        assert "user_id" not in row or row["user_id"] is None

    @pytest.mark.asyncio
    async def test_existing_row_untouched_except_suspended_flag(self):
        db = _FakeDB()
        db.github_installations.rows.append({
            "installation_id": 555, "user_id": "real-user", "active": True,
            "suspended_at": None, "repositories": [{"full_name": "a/b"}],
            "installed_at": time.time() - 1000,
        })
        live = [{"id": 555, "account": {"login": "x"}, "suspended_at": None}]
        with patch.object(ga, "list_installations", AsyncMock(return_value=live)):
            result = await ga.reconcile_installations(db)
        assert result["added"] == []
        assert result["resynced"] == []
        row = db.github_installations.rows[0]
        # Never touched — reconcile must not clobber an already-linked row.
        assert row["user_id"] == "real-user"
        assert row["repositories"] == [{"full_name": "a/b"}]

    @pytest.mark.asyncio
    async def test_resyncs_suspended_flag_when_github_reports_suspended(self):
        db = _FakeDB()
        db.github_installations.rows.append({
            "installation_id": 777, "user_id": "real-user-2", "active": True,
            "suspended_at": None, "installed_at": time.time() - 1000,
        })
        live = [{"id": 777, "account": {"login": "y"}, "suspended_at": "2026-09-16T00:00:00Z"}]
        with patch.object(ga, "list_installations", AsyncMock(return_value=live)):
            result = await ga.reconcile_installations(db)
        assert result["resynced"] == [777]
        row = db.github_installations.rows[0]
        assert row["active"] is False
        assert row["suspended_at"] is not None
        assert row["user_id"] == "real-user-2"   # untouched

    @pytest.mark.asyncio
    async def test_github_list_failure_returns_ok_false_not_raise(self):
        db = _FakeDB()
        with patch.object(ga, "list_installations", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await ga.reconcile_installations(db)
        assert result["ok"] is False
        assert "boom" in result["error"] or "RuntimeError" in result["error"]

    @pytest.mark.asyncio
    async def test_never_calls_list_installation_repos_per_item(self):
        """Regression guard for the live-measured perf bug: an earlier
        draft fetched each ghost row's repo list synchronously (dozens
        of sequential GitHub round-trips per admin HTTP call) and
        tripped the `github` circuit breaker after ~5 timeouts. The
        shipped version never calls list_installation_repos at all —
        repos self-heal lazily via /status once linked."""
        db = _FakeDB()
        live = [{"id": i, "account": {"login": f"user{i}"}, "suspended_at": None}
                for i in range(1, 21)]
        with patch.object(ga, "list_installations", AsyncMock(return_value=live)), \
             patch.object(ga, "list_installation_repos", AsyncMock(
                 side_effect=AssertionError("must not be called"))):
            result = await ga.reconcile_installations(db)
        assert result["ok"] is True
        assert len(result["added"]) == 20


# ═════════════════════════════════════════════════════════════════════
# 3. webhook_fence_status(event_filter=...)
# ═════════════════════════════════════════════════════════════════════

class TestWebhookFenceEventFilter:
    @pytest.fixture(autouse=True)
    def _configured(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PrivateFormat, NoEncryption,
        )
        from services.github_app_config import set_runtime_github_app_config
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv_pem = key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
        ).decode()
        set_runtime_github_app_config({
            "app_id": "123456", "app_slug": "aurem-test",
            "private_key": priv_pem, "webhook_secret": "whsec",
        })
        ga._APP_JWT_CACHE = None
        yield
        set_runtime_github_app_config(None)
        ga._APP_JWT_CACHE = None

    def _mock_client_factory(self, pages):
        """pages: list of (deliveries_list, has_next_page)."""
        _RealAsyncClient = httpx.AsyncClient
        state = {"page": 0}

        def handler(request):
            path = request.url.path
            if path == "/app":
                return httpx.Response(200, json={"events": ["pull_request"]})
            if path == "/app/hook/deliveries":
                i = state["page"]
                state["page"] += 1
                deliveries, has_next = pages[min(i, len(pages) - 1)]
                headers = {}
                if has_next:
                    headers["link"] = '<https://api.github.com/app/hook/deliveries?cursor=next>; rel="next"'
                return httpx.Response(200, json=deliveries, headers=headers)
            return httpx.Response(404)

        def factory(*a, **kw):
            return _RealAsyncClient(transport=httpx.MockTransport(handler))
        return factory

    @pytest.mark.asyncio
    async def test_unfiltered_returns_first_page_only(self, monkeypatch):
        page1 = [{"id": 1, "event": "workflow_job", "status_code": 200}]
        factory = self._mock_client_factory([(page1, True)])
        with patch.object(httpx, "AsyncClient", factory):
            result = await ga.webhook_fence_status(recent_limit=15)
        assert result["ok"] is True
        assert len(result["recent_deliveries"]) == 1
        assert result["recent_deliveries"][0]["event"] == "workflow_job"

    @pytest.mark.asyncio
    async def test_event_filter_scans_multiple_pages_for_match(self, monkeypatch):
        page1 = [{"id": i, "event": "workflow_job", "status_code": 200} for i in range(100)]
        page2 = [{"id": 200, "event": "installation", "action": "created", "status_code": 200}]
        factory = self._mock_client_factory([(page1, True), (page2, True), ([], False)])
        with patch.object(httpx, "AsyncClient", factory):
            result = await ga.webhook_fence_status(
                recent_limit=10, event_filter="installation", scan_limit=5,
            )
        assert result["event_filter"] == "installation"
        assert len(result["recent_deliveries"]) == 1
        assert result["recent_deliveries"][0]["event"] == "installation"

    @pytest.mark.asyncio
    async def test_event_filter_stops_at_scan_limit_when_nothing_matches(self, monkeypatch):
        noise = [{"id": i, "event": "workflow_job", "status_code": 200} for i in range(100)]
        factory = self._mock_client_factory([(noise, True)])  # always has_next -> would loop forever
        with patch.object(httpx, "AsyncClient", factory):
            result = await ga.webhook_fence_status(
                recent_limit=10, event_filter="installation", scan_limit=3,
            )
        assert result["ok"] is True
        assert result["recent_deliveries"] == []


# ═════════════════════════════════════════════════════════════════════
# 4. Admin endpoints (routers/admin_ops_config.py)
# ═════════════════════════════════════════════════════════════════════

import routers.admin_ops_config as ops_config


@pytest.fixture(autouse=True)
def _stub_admin(monkeypatch):
    async def _fake_require_admin(authorization):
        return {"email": "founder@aurem.dev", "user_id": "u1"}
    monkeypatch.setattr(ops_config, "_require_admin", _fake_require_admin)


class TestUnlinkedAdminEndpoint:
    @pytest.mark.asyncio
    async def test_lists_only_unlinked_rows_and_runs_reconcile_first(self, monkeypatch):
        db = _FakeDB()
        db.github_installations.rows.append({
            "installation_id": 1, "user_id": "linked-user", "active": True,
            "installed_at": time.time(), "updated_at": time.time(),
        })
        db.github_installations.rows.append({
            "installation_id": 2, "active": True,
            "installed_at": time.time(), "updated_at": time.time(),
        })
        monkeypatch.setattr(ops_config, "get_db", lambda: db)
        reconcile_mock = AsyncMock(return_value={"ok": True, "checked": 2, "added": [], "resynced": [], "errors": []})
        monkeypatch.setattr(ga, "reconcile_installations", reconcile_mock)

        out = await ops_config.github_installations_unlinked(authorization="Bearer x")
        reconcile_mock.assert_awaited_once()
        assert out["ok"] is True
        assert len(out["rows"]) == 1
        assert out["rows"][0]["installation_id"] == 2

    @pytest.mark.asyncio
    async def test_reconcile_first_false_skips_the_pass(self, monkeypatch):
        db = _FakeDB()
        monkeypatch.setattr(ops_config, "get_db", lambda: db)
        reconcile_mock = AsyncMock()
        monkeypatch.setattr(ga, "reconcile_installations", reconcile_mock)
        out = await ops_config.github_installations_unlinked(
            authorization="Bearer x", reconcile_first=False,
        )
        reconcile_mock.assert_not_awaited()
        assert out["reconcile"] is None


class TestReconcileTriggerEndpoint:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_returns_result(self, monkeypatch):
        db = _FakeDB()
        monkeypatch.setattr(ops_config, "get_db", lambda: db)
        expected = {"ok": True, "checked": 5, "added": [9], "resynced": [], "errors": []}
        monkeypatch.setattr(ga, "reconcile_installations", AsyncMock(return_value=expected))
        out = await ops_config.github_installations_reconcile(authorization="Bearer x")
        assert out == expected


class TestManualLinkEndpoint:
    @pytest.mark.asyncio
    async def test_links_unlinked_installation_to_user_by_email(self, monkeypatch):
        db = _FakeDB()
        db.github_installations.rows.append({"installation_id": 42, "active": True})
        db.dev_users.rows.append({"user_id": "u_rebecca", "email": "rebecca@millingtonstudio.com"})
        monkeypatch.setattr(ops_config, "get_db", lambda: db)

        body = ops_config.GithubInstallLinkBody(email="rebecca@millingtonstudio.com")
        out = await ops_config.github_installation_link(42, body, authorization="Bearer x")

        assert out["ok"] is True
        assert out["linked_user_id"] == "u_rebecca"
        row = db.github_installations.rows[0]
        assert row["user_id"] == "u_rebecca"
        audit = db.admin_audit.rows[0]
        assert audit["action"] == "manual_github_install_link"
        assert audit["installation_id"] == 42

    @pytest.mark.asyncio
    async def test_404_when_installation_not_found(self, monkeypatch):
        db = _FakeDB()
        monkeypatch.setattr(ops_config, "get_db", lambda: db)
        from fastapi import HTTPException
        body = ops_config.GithubInstallLinkBody(email="x@y.com")
        with pytest.raises(HTTPException) as exc:
            await ops_config.github_installation_link(999, body, authorization="Bearer x")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_409_when_already_linked_to_someone(self, monkeypatch):
        db = _FakeDB()
        db.github_installations.rows.append({"installation_id": 42, "user_id": "already-someone"})
        monkeypatch.setattr(ops_config, "get_db", lambda: db)
        from fastapi import HTTPException
        body = ops_config.GithubInstallLinkBody(email="x@y.com")
        with pytest.raises(HTTPException) as exc:
            await ops_config.github_installation_link(42, body, authorization="Bearer x")
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_404_when_email_does_not_match_any_user(self, monkeypatch):
        db = _FakeDB()
        db.github_installations.rows.append({"installation_id": 42})
        monkeypatch.setattr(ops_config, "get_db", lambda: db)
        from fastapi import HTTPException
        body = ops_config.GithubInstallLinkBody(email="nobody@nowhere.com")
        with pytest.raises(HTTPException) as exc:
            await ops_config.github_installation_link(42, body, authorization="Bearer x")
        assert exc.value.status_code == 404
