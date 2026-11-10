"""tests/test_regression_iter2026_19c_save_to_github_no_502.py

Part B follow-up — "Save to GitHub" modal shows a raw Cloudflare error
page ("origin web server returned an invalid or incomplete response").

ROOT CAUSE (diagnosed live, 2026-10-27): all three GitHub-failure call
sites — `GET /cto/projects/{id}/github/repos`, `.../github/branches`,
and `POST /cto/tasks/submit`'s ship_target repo check — raised
`HTTPException(502, classify_error(e)["user_message"])`. Cloudflare
(and most edge/CDN layers) REPLACE the response BODY of a
502/503/504 with their own HTML/text before it reaches the browser —
confirmed by `routers/cto_projects/management.py`'s own 2026-02
comment on the identical failure mode for `/simplify`, which is why
that endpoint already uses 424. This file proves (a) the two read
endpoints and the submit-time check now use the same 424 pass-through
pattern with a structured, leak-free detail, (b) `list_installation_repos`
gained a retry-on-timeout, a page cap, an overall deadline, and a
short TTL cache (so a modal open immediately followed by Save doesn't
re-list the same installation's repos twice), and (c) a wallet hold
taken by `assert_wallet` is released if `submit_task` fails before a
task row is ever queued.

Could NOT reproduce the live incident end-to-end against this pod
(no backend logs showed a `list_installation_repos failed` entry in
this environment's history, and a direct repro against a live
installation succeeded) — the diagnosis is a static-code root cause
(the 502-body-replaced-by-edge-CDN behavior is independently confirmed
by the pre-existing `management.py` comment + GitHub's and
Cloudflare's own documented behavior), not a live-captured traceback.
Reported honestly in the final report's "Not tested" section.

Note: numbered 19c (not 10h) — tests/test_regression_iter2026_10h_
wallet_hold_settle_release.py already occupies the 10h slot.
"""
from __future__ import annotations

import asyncio
import time
import types
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── shared fake-DB harness (same shape as test_regression_iter2026_10g) ──

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if row.get(k) != v:
                return False
        return True

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return types.SimpleNamespace(inserted_id="new")

    async def find_one_and_update(self, query, update, projection=None):
        return None

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


USER = {"user_id": "u1", "email": "user@example.com", "tier": "pro",
        "is_admin": False, "is_unlimited": False, "track": "dev",
        "created_at": time.time()}
USER_B = {"user_id": "u2", "email": "other@example.com", "tier": "pro",
          "is_admin": False, "is_unlimited": False, "track": "dev",
          "created_at": time.time()}

AUTH = {"Authorization": "Bearer u1"}
AUTH_B = {"Authorization": "Bearer u2"}

PROJ = {
    "project_id": "p1", "user_id": "u1",
    "github_owner": "acme", "github_repo": "widgets", "branch": "main",
    "installation_id": "999",
}
PROJ_B = {
    "project_id": "p2", "user_id": "u2",
    "github_owner": "bco", "github_repo": "secret-repo", "branch": "main",
    "installation_id": "888",
}

REPO_LIST = [
    {"full_name": "acme/widgets", "private": False, "default_branch": "main"},
]


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture(autouse=True)
def _reset_repo_list_cache():
    """The new TTL cache in services.github_app is a process-wide
    singleton — clear it before AND after every test in this file so
    no test's cached repo list leaks into another's assertions (same
    failure class services.retry_guard.reset_breakers_for_tests and
    services.rate_limiter's in-memory buckets already guard against)."""
    from services.github_app import reset_repo_list_cache_for_tests
    reset_repo_list_cache_for_tests()
    yield
    reset_repo_list_cache_for_tests()


@pytest.fixture
def client(fake_db):
    from cto_services import db as _dbmod
    from routers import cto_projects as router_mod
    _dbmod.set_db(fake_db)

    users_by_token = {"u1": USER, "u2": USER_B}

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        token = authorization.replace("Bearer ", "")
        user = users_by_token.get(token)
        if not user:
            from fastapi import HTTPException as _HE
            raise _HE(401, "invalid token")
        return user

    old_current_dev = router_mod.current_dev
    router_mod.current_dev = _fake_current_dev

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    router_mod.current_dev = old_current_dev
    _dbmod.set_db(None)


def _submit_body(**kw):
    base = {"project_id": "p1", "task": "fix the bug in signup.py"}
    base.update(kw)
    return base


# ═══════════════════════════════════════════════════════════════════
# 1. No more 502 — structured, leak-free detail on every GitHub
#    failure, at all three call sites.
# ═══════════════════════════════════════════════════════════════════

class TestNo502OnGitHubFailure:
    def test_list_repos_github_failure_is_424_not_502(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        boom = httpx.TimeoutException("connect timed out")
        with patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(side_effect=boom)):
            r = client.get("/api/aurem-dev/cto/projects/p1/github/repos", headers=AUTH)
        assert r.status_code == 424, r.text
        assert r.status_code not in (502, 503, 504)
        body = r.json()["detail"]
        assert isinstance(body, dict)
        assert body["code"] == "github_timeout"
        assert "retryable" in body
        # No raw exception text, no stack trace, no token.
        assert "Traceback" not in str(body)
        assert "tok" not in str(body)
        assert "TimeoutException" not in str(body)

    def test_list_branches_github_failure_is_424_not_502(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        boom = httpx.TimeoutException("connect timed out")
        with patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(return_value=REPO_LIST)), \
             patch("services.loop_safety.list_repo_branches",
                   AsyncMock(side_effect=boom)):
            r = client.get(
                "/api/aurem-dev/cto/projects/p1/github/branches",
                headers=AUTH, params={"owner": "acme", "repo": "widgets"},
            )
        assert r.status_code == 424, r.text
        body = r.json()["detail"]
        assert isinstance(body, dict)
        assert body["code"] == "github_timeout"

    def test_list_branches_endpoint_repo_listing_failure_is_424(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        boom = httpx.TimeoutException("connect timed out")
        with patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(side_effect=boom)):
            r = client.get(
                "/api/aurem-dev/cto/projects/p1/github/branches",
                headers=AUTH, params={"owner": "acme", "repo": "widgets"},
            )
        assert r.status_code == 424, r.text

    def test_submit_task_github_failure_is_424_not_502(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        boom = httpx.TimeoutException("connect timed out")
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("services.github_app.list_installation_repos", AsyncMock(side_effect=boom)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(repo="acme/widgets", branch="auremcto/ship-x"))
        assert r.status_code == 424, r.text
        assert isinstance(r.json()["detail"], dict)
        assert fake_db.cto_tasks.rows == [], "a failed submit must insert no task row"

    def test_breaker_open_also_maps_to_424(self, client, fake_db):
        """A fully-open circuit breaker (sustained outage) must also
        surface as 424, never a raw 503 the edge layer could mangle
        the same way."""
        from services.retry_guard import BreakerOpenError
        fake_db.cto_projects.rows.append(dict(PROJ))
        with patch("services.pat_vault.get_repo_token_or_error",
                   AsyncMock(return_value=("tok", None, None))), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(side_effect=BreakerOpenError("github", 42.0))):
            r = client.get("/api/aurem-dev/cto/projects/p1/github/repos", headers=AUTH)
        assert r.status_code == 424, r.text
        assert r.json()["detail"]["code"] == "github_unavailable"


# ═══════════════════════════════════════════════════════════════════
# 2. classify_github_failure — unit coverage, every branch.
# ═══════════════════════════════════════════════════════════════════

class TestClassifyGithubFailure:
    def _http_err(self, status, text=""):
        req = httpx.Request("GET", "https://api.github.com/x")
        resp = httpx.Response(status, request=req, text=text)
        return httpx.HTTPStatusError("boom", request=req, response=resp)

    @pytest.mark.parametrize("status,expected_code", [
        (401, "github_auth_rejected"),
        (403, "github_auth_rejected"),
        (404, "github_not_found"),
        (429, "github_rate_limited"),
        (500, "github_upstream_error"),
        (502, "github_upstream_error"),
        (422, "github_rejected"),
    ])
    def test_http_status_error_branches(self, status, expected_code):
        from services.github_errors import classify_github_failure
        out = classify_github_failure(self._http_err(status))
        assert out["code"] == expected_code
        assert isinstance(out["message"], str) and out["message"]
        assert isinstance(out["retryable"], bool)

    def test_timeout_classified_retryable(self):
        from services.github_errors import classify_github_failure
        out = classify_github_failure(httpx.TimeoutException("t"))
        assert out["code"] == "github_timeout"
        assert out["retryable"] is True

    def test_network_error_classified_retryable(self):
        from services.github_errors import classify_github_failure
        out = classify_github_failure(httpx.ConnectError("c"))
        assert out["code"] == "github_timeout"
        assert out["retryable"] is True

    def test_breaker_open_classified(self):
        from services.github_errors import classify_github_failure
        from services.retry_guard import BreakerOpenError
        out = classify_github_failure(BreakerOpenError("github", 10.0))
        assert out["code"] == "github_unavailable"
        assert out["retryable"] is True

    def test_unknown_exception_falls_back_to_generic(self):
        from services.github_errors import classify_github_failure
        out = classify_github_failure(ValueError("some internal detail, token=abc123"))
        assert out["code"] == "github_error"
        # Never leaks the raw exception text.
        assert "token=abc123" not in out["message"]

    def test_status_code_never_leaks_into_message(self):
        """Secret/URL/stack-trace leak check — the message must never
        contain the raw exception repr, a URL, or anything resembling
        a token."""
        from services.github_errors import classify_github_failure
        req = httpx.Request("GET", "https://api.github.com/installation/repositories",
                             headers={"Authorization": "Bearer ghs_supersecrettoken123"})
        resp = httpx.Response(404, request=req, text="ghs_supersecrettoken123 not found")
        exc = httpx.HTTPStatusError("boom", request=req, response=resp)
        out = classify_github_failure(exc)
        assert "ghs_supersecrettoken123" not in out["message"]
        assert "api.github.com" not in out["message"]


# ═══════════════════════════════════════════════════════════════════
# 3. list_installation_repos — deadline, retry, pagination cap, cache.
# ═══════════════════════════════════════════════════════════════════

class TestListInstallationReposHardening:
    @pytest.mark.asyncio
    async def test_retries_once_on_timeout_then_succeeds(self):
        from services import github_app as ga

        calls = {"n": 0}

        class _FakeResp:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {"repositories": [{"full_name": "acme/widgets"}]}

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise httpx.TimeoutException("blip")
                return _FakeResp()

        with patch.object(ga, "get_installation_token", AsyncMock(return_value=("tok", 0.0))), \
             patch.object(ga, "ext_client", lambda *a, **k: _FakeClient()):
            out = await ga.list_installation_repos(111)
        assert out == [{"full_name": "acme/widgets"}]
        assert calls["n"] == 2, "must retry exactly once after the first transient timeout"

    @pytest.mark.asyncio
    async def test_pagination_capped_so_a_malicious_link_header_cannot_loop_forever(self):
        from services import github_app as ga

        calls = {"n": 0}

        class _FakeResp:
            status_code = 200

            def __init__(self, page):
                self._page = page

            @property
            def headers(self):
                # Always claims there's a next page — proves the cap
                # (not the data) is what stops this, not EOF.
                return {"link": '<https://api.github.com/next>; rel="next"'}

            def raise_for_status(self):
                return None

            def json(self):
                return {"repositories": [{"full_name": f"acme/repo-{self._page}"}]}

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                calls["n"] += 1
                return _FakeResp(calls["n"])

        with patch.object(ga, "get_installation_token", AsyncMock(return_value=("tok", 0.0))), \
             patch.object(ga, "ext_client", lambda *a, **k: _FakeClient()):
            out = await ga.list_installation_repos(222)
        assert calls["n"] == ga._REPO_LIST_MAX_PAGES
        assert len(out) == ga._REPO_LIST_MAX_PAGES

    @pytest.mark.asyncio
    async def test_overall_deadline_abandons_a_stalled_fetch(self):
        from services import github_app as ga

        class _HangingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                await asyncio.sleep(5)  # longer than the test's forced deadline

        with patch.object(ga, "get_installation_token", AsyncMock(return_value=("tok", 0.0))), \
             patch.object(ga, "ext_client", lambda *a, **k: _HangingClient()), \
             patch.object(ga, "_REPO_LIST_OVERALL_DEADLINE_SECONDS", 0.05):
            with pytest.raises(asyncio.TimeoutError):
                await ga.list_installation_repos(333)

    @pytest.mark.asyncio
    async def test_successful_result_is_cached_per_installation(self):
        from services import github_app as ga

        calls = {"n": 0}

        class _FakeResp:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {"repositories": [{"full_name": "acme/widgets"}]}

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                calls["n"] += 1
                return _FakeResp()

        with patch.object(ga, "get_installation_token", AsyncMock(return_value=("tok", 0.0))), \
             patch.object(ga, "ext_client", lambda *a, **k: _FakeClient()):
            first = await ga.list_installation_repos(444)
            second = await ga.list_installation_repos(444)
        assert first == second == [{"full_name": "acme/widgets"}]
        assert calls["n"] == 1, (
            "the second call within the TTL window must be served from cache — "
            "this is the fix for 'submit_task re-lists the repos even though the "
            "modal just fetched the same list'"
        )

    @pytest.mark.asyncio
    async def test_cache_is_scoped_per_installation_not_shared(self):
        """IDOR-adjacent check: installation A's cached repo list must
        never be returned for installation B."""
        from services import github_app as ga

        class _FakeResp:
            status_code = 200
            headers = {}

            def __init__(self, name):
                self._name = name

            def raise_for_status(self):
                return None

            def json(self):
                return {"repositories": [{"full_name": self._name}]}

        class _FakeClientA:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                return _FakeResp("acme/widgets")

        class _FakeClientB:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                return _FakeResp("bco/secret-repo")

        with patch.object(ga, "get_installation_token", AsyncMock(return_value=("tok", 0.0))), \
             patch.object(ga, "ext_client", lambda *a, **k: _FakeClientA()):
            repos_a = await ga.list_installation_repos(111)
        with patch.object(ga, "get_installation_token", AsyncMock(return_value=("tok", 0.0))), \
             patch.object(ga, "ext_client", lambda *a, **k: _FakeClientB()):
            repos_b = await ga.list_installation_repos(222)
        assert repos_a == [{"full_name": "acme/widgets"}]
        assert repos_b == [{"full_name": "bco/secret-repo"}]

    @pytest.mark.asyncio
    async def test_failure_is_never_cached(self):
        from services import github_app as ga

        calls = {"n": 0}

        class _FakeResp:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {"repositories": [{"full_name": "acme/widgets"}]}

        class _FlakyClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                calls["n"] += 1
                if calls["n"] <= 3:  # exhaust the 2 retries too
                    raise httpx.TimeoutException("down")
                return _FakeResp()

        with patch.object(ga, "get_installation_token", AsyncMock(return_value=("tok", 0.0))), \
             patch.object(ga, "ext_client", lambda *a, **k: _FlakyClient()):
            with pytest.raises(httpx.TimeoutException):
                await ga.list_installation_repos(555)
            # Second call must try again (no cache entry from the failure).
            out = await ga.list_installation_repos(555)
        assert out == [{"full_name": "acme/widgets"}]


# ═══════════════════════════════════════════════════════════════════
# 4. Wallet-hold leak: a failure after assert_wallet must release.
# ═══════════════════════════════════════════════════════════════════

class TestWalletHoldReleasedOnSubmitFailure:
    def test_insert_failure_after_hold_releases_the_hold(self, client, fake_db, monkeypatch):
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_db.dev_users.rows.append({"user_id": "u1", "fix_entitlement": True})
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        released = {}

        async def _fake_release(db, *, user_id, run_id, idempotency_key, reason=""):
            released["called"] = True
            released["user_id"] = user_id
            released["reason"] = reason
            return {"ok": True, "released": 100}

        async def _boom_insert(doc):
            raise RuntimeError("duplicate key / transient mongo error")

        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("services.wallet.assert_wallet",
                   AsyncMock(return_value={"mode": "on", "held": True})), \
             patch("services.wallet.release", _fake_release), \
             patch.object(fake_db.cto_tasks, "insert_one", _boom_insert):
            with pytest.raises(RuntimeError):
                client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                            json=_submit_body())
        assert released.get("called") is True, (
            "a wallet hold taken by assert_wallet must be released when the "
            "task row insert fails before any task is ever queued for "
            "_run_task's own finally-block to release it"
        )
        assert released["user_id"] == "u1"
        assert fake_db.cto_tasks.rows == []

    def test_release_failure_does_not_mask_the_original_insert_error(self, client, fake_db, monkeypatch):
        """2026-10-27 — testing-agent finding: if `release()` itself
        raised inside the `except` block, Python's `raise` there used
        to swap the active exception — the REAL root cause of the
        failed submit (e.g. a duplicate task_id) would be lost from
        the logs entirely, replaced by an unrelated wallet error."""
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_db.dev_users.rows.append({"user_id": "u1", "fix_entitlement": True})
        fake_ctx = type("Ctx", (), {"pat": "tok"})()

        async def _boom_insert(doc):
            raise RuntimeError("the REAL root cause: duplicate task_id")

        async def _release_also_fails(db, *, user_id, run_id, idempotency_key, reason=""):
            raise ConnectionError("wallet service unreachable")

        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("services.wallet.assert_wallet",
                   AsyncMock(return_value={"mode": "on", "held": True})), \
             patch("services.wallet.release", _release_also_fails), \
             patch.object(fake_db.cto_tasks, "insert_one", _boom_insert):
            with pytest.raises(RuntimeError, match="the REAL root cause"):
                client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                            json=_submit_body())


# ═══════════════════════════════════════════════════════════════════
# 5. IDOR — a repo/branch check always resolves through the CALLER's
#    OWN project/installation, never another user's.
# ═══════════════════════════════════════════════════════════════════

class TestIDOR:
    def test_user_a_cannot_list_repos_for_user_bs_project(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_db.cto_projects.rows.append(dict(PROJ_B))
        r = client.get("/api/aurem-dev/cto/projects/p2/github/repos", headers=AUTH)
        assert r.status_code == 404, (
            "user u1 must not be able to probe user u2's project p2 at all"
        )

    def test_user_a_cannot_list_branches_for_user_bs_project(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_db.cto_projects.rows.append(dict(PROJ_B))
        r = client.get(
            "/api/aurem-dev/cto/projects/p2/github/branches",
            headers=AUTH, params={"owner": "bco", "repo": "secret-repo"},
        )
        assert r.status_code == 404

    def test_user_a_cannot_submit_a_task_targeting_user_bs_project(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_db.cto_projects.rows.append(dict(PROJ_B))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(project_id="p2"))
        assert r.status_code == 404
        assert fake_db.cto_tasks.rows == []

    def test_user_a_cannot_target_user_bs_repo_via_its_own_project(self, client, fake_db):
        """Even on user u1's OWN project, `repo=` cannot point at a
        repo only installation 888 (user u2's) can see — the
        writable-repo-list check resolves through project p1's own
        `installation_id` (999), never a client-supplied one."""
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("services.github_app.list_installation_repos",
                   AsyncMock(return_value=REPO_LIST)):  # installation 999's own list
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(repo="bco/secret-repo", branch="auremcto/ship-x"))
        assert r.status_code == 403
        assert fake_db.cto_tasks.rows == []


# ═══════════════════════════════════════════════════════════════════
# 6. Mongo-operator-injection + mass-assignment on the new fields.
# ═══════════════════════════════════════════════════════════════════

class TestInjectionAndMassAssignment:
    def test_repo_as_mongo_operator_rejected_by_schema_not_by_mongo(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                         json={"project_id": "p1", "task": "x",
                               "repo": {"$ne": None}, "branch": "auremcto/ship-x"})
        assert r.status_code == 422, (
            "repo is typed `str | None` — a dict payload must be rejected by "
            "Pydantic validation before it can ever reach a Mongo query"
        )
        assert fake_db.cto_tasks.rows == []

    def test_branch_as_mongo_operator_rejected_by_schema(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                         json={"project_id": "p1", "task": "x",
                               "branch": {"$gt": ""}})
        assert r.status_code == 422

    def test_extra_client_supplied_fields_are_ignored_not_mass_assigned(self, client, fake_db):
        """Pydantic's default `extra` behavior (ignore) means a client
        cannot smuggle e.g. `user_id` or `status` into the stored task
        doc through the request body."""
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(user_id="u2", status="done"))
        assert r.status_code == 200, r.text
        stored = fake_db.cto_tasks.rows[0]
        assert stored["user_id"] == "u1", "client-supplied user_id must never override the real caller"
        assert stored["status"] == "queued", "client-supplied status must never override the real initial state"


# ═══════════════════════════════════════════════════════════════════
# 7. Branch-name validation hardening (leading '-', etc).
# ═══════════════════════════════════════════════════════════════════

class TestBranchValidationHardening:
    @pytest.mark.parametrize("bad_branch", [
        "-ship-fix", "--force", "-", "a/../b", "a b", "/leading-slash",
        "trailing-slash/", "a@{b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b",
    ])
    def test_rejected_branch_names(self, bad_branch):
        from services.loop_safety import validate_branch_name
        assert validate_branch_name(bad_branch) is not None

    @pytest.mark.parametrize("good_branch", [
        "auremcto/ship-abc123", "feature/my-fix", "main", "release-1.2.3",
    ])
    def test_accepted_branch_names(self, good_branch):
        from services.loop_safety import validate_branch_name
        assert validate_branch_name(good_branch) is None

    def test_leading_dash_branch_rejected_via_http(self, client, fake_db):
        fake_db.cto_projects.rows.append(dict(PROJ))
        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)):
            r = client.post("/api/aurem-dev/cto/tasks/submit", headers=AUTH,
                             json=_submit_body(branch="-rf"))
        assert r.status_code == 400
        assert fake_db.cto_tasks.rows == []


# ═══════════════════════════════════════════════════════════════════
# 8. Concurrency — two concurrent submits never double-hold/corrupt.
# ═══════════════════════════════════════════════════════════════════

class TestConcurrentSubmit:
    @pytest.mark.asyncio
    async def test_two_concurrent_submits_each_get_their_own_hold_and_task(self, fake_db):
        """Each submit mints its own task_id (uuid4), so two concurrent
        `/tasks/submit` calls from the same user are two independent
        wallet holds against two independent task rows — never a
        shared idempotency_key that could double-spend OR silently
        drop one of the two."""
        from routers.cto_projects.tasks import submit_task, TaskBody
        from cto_services import db as _dbmod
        import routers.cto_projects as router_mod
        _dbmod.set_db(fake_db)
        fake_db.cto_projects.rows.append(dict(PROJ))

        async def _fake_current_dev(authorization=None):
            return USER
        router_mod.current_dev = _fake_current_dev

        fake_ctx = type("Ctx", (), {"pat": "tok"})()
        from fastapi import BackgroundTasks

        class _FakeRequest:
            client = types.SimpleNamespace(host="1.2.3.4")
            headers = {}

        with patch("routers.cto_projects.assert_has_budget", AsyncMock(return_value=None)), \
             patch("routers.cto_projects.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.rate_limiter.check_rate_limit_async", AsyncMock(return_value=True)), \
             patch("services.ora_context.build_ora_context", AsyncMock(return_value=fake_ctx)), \
             patch("routers.cto_projects._run_task", AsyncMock(return_value=None)):
            body1 = TaskBody(project_id="p1", task="fix the signup bug")
            body2 = TaskBody(project_id="p1", task="fix the login bug")
            r1, r2 = await asyncio.gather(
                submit_task(_FakeRequest(), body1, BackgroundTasks(), "Bearer u1"),
                submit_task(_FakeRequest(), body2, BackgroundTasks(), "Bearer u1"),
            )
        assert r1["ok"] is True and r2["ok"] is True
        assert r1["task_id"] != r2["task_id"]
        assert len(fake_db.cto_tasks.rows) == 2
        _dbmod.set_db(None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
