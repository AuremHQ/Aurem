"""
Iter 212m-126 — Auto-heal pipeline (App-only world, 2026-06 PAT removal).

Heal strategies under test:
  • network:         retry with backoff → succeeds on 3rd try
  • network, no tok: returns a finalised dict (regression: was an
                     un-awaited coroutine)
  • github_rejected: retry once with a fresh installation token
  • repo_not_found:  lookup user repos → rename detection + db update
  • no_token:        App reinstall required (no OAuth fallback anymore)

Operational guarantees:
  • per-project 5-minute cooldown blocks back-to-back heals
  • success invalidates the connection-status cache
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
    def json(self): return self._body


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.updates = []
        self.inserts = []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                return dict(r)
        return None
    async def update_one(self, q, update, upsert=False):
        self.updates.append({"q": q, "update": update})
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
        return None
    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        return None


class _FakeDB:
    def __init__(self, proj):
        self.cto_projects = _FakeColl([proj])
        self.repo_heal_audit = _FakeColl()


class _FakeClient:
    """Stand-in for the httpx.AsyncClient yielded by `ext_client`.
    `plan` is a list of `(matcher(url, headers) -> bool, resp|exc)`."""
    def __init__(self, plan):
        self.plan = plan
        self.calls = []
    async def get(self, url, headers=None):
        self.calls.append({"url": url, "auth": (headers or {}).get("Authorization", "")})
        for matcher, resp in self.plan:
            if matcher(url, headers or {}):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return _Resp(500)


def _make_db(*, owner="acme", repo="api"):
    return _FakeDB(proj={
        "user_id": "u1", "project_id": "p1", "name": "demo",
        "github_owner": owner, "github_repo": repo,
        "auth_method": "github_app", "installation_id": 42,
    })


def _run(db, client, *, token="ghs_app", error):
    @asynccontextmanager
    async def fake_ext_client(dep, **kw):
        assert dep == "github"
        yield client

    with patch("services.repo_heal.ext_client", fake_ext_client), \
         patch("services.pat_vault.get_repo_token_or_error",
               AsyncMock(return_value=(token, None if token else "no_token", None))), \
         patch("services.retry_guard.asyncio.sleep", AsyncMock()):
        from services.repo_heal import heal_project
        return asyncio.run(heal_project(
            db=db, user_id="u1", project_id="p1",
            prior_status={"error": error, "auth": "app"},
        ))


@pytest.fixture(autouse=True)
def _clear_caches():
    from services import repo_heal as rh
    rh._last_heal_at.clear()
    rh._inflight.clear()
    rh._cooldown_until.clear()


# ─── 1) network error → retry with backoff → recovers ─────────────
def test_network_retry_recovers():
    db = _make_db()
    attempts = {"n": 0}
    def contents(url, h): return url.endswith("/contents/")
    def flaky(url, h):
        attempts["n"] += 1
        return attempts["n"] <= 2
    client = _FakeClient([
        (lambda u, h: contents(u, h) and flaky(u, h), httpx.TimeoutException("simulated")),
        (contents, _Resp(200)),
    ])
    result = _run(db, client, error="network: TimeoutException")
    assert result["ok"] is True
    assert result["reason"] == "network_retry_recovered"
    assert attempts["n"] == 3
    assert db.repo_heal_audit.inserts[-1]["success"] is True


def test_network_without_token_returns_finalised_dict():
    """Regression: this branch used to `return _finalise(...)` without
    awaiting — callers got a coroutine, never an outcome dict."""
    db = _make_db()
    result = _run(db, _FakeClient([]), token=None, error="network: TimeoutException")
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["reason"] == "no_token_for_retry"
    from services import repo_heal as rh
    assert rh._cooldown_until.get("p1", 0) > time.time()   # permanent-failure block


# ─── 2) token rejected → fresh installation token works ───────────
def test_github_rejected_retries_with_fresh_app_token():
    db = _make_db()
    def is_app(url, h): return "token ghs_app" in h.get("Authorization", "")
    client = _FakeClient([(is_app, _Resp(200))])
    result = _run(db, client, error="github_rejected")
    assert result["ok"] is True
    assert result["reason"] == "fresh_app_token_works"


def test_github_rejected_without_token_needs_reinstall():
    result = _run(_make_db(), _FakeClient([]), token=None, error="github_rejected")
    assert result["ok"] is False
    assert result["reason"] == "app_reinstall_required"


# ─── 3) 404 → rename lookup updates owner/repo ───────────────────
def test_repo_renamed_updates_project():
    db = _make_db(owner="oldowner", repo="oldrepo")
    list_resp = _Resp(200, body=[
        {"name": "different", "full_name": "x/different"},
        {"name": "oldrepo",   "full_name": "newowner/newname"},
    ])
    client = _FakeClient([(lambda u, h: "/user/repos" in u, list_resp)])
    result = _run(db, client, error="repo_not_found")
    assert result["ok"] is True
    assert "repo_renamed_to:newowner/newname" in result["reason"]
    row = db.cto_projects.rows[0]
    assert row["github_owner"] == "newowner"
    assert row["github_repo"]  == "newname"
    assert row["renamed_from"] == "oldowner/oldrepo"


def test_repo_gone_is_permanent_failure():
    client = _FakeClient([(lambda u, h: "/user/repos" in u, _Resp(200, body=[]))])
    result = _run(_make_db(), client, error="repo_not_found")
    assert result["ok"] is False
    assert result["reason"] == "repo_gone_or_no_access"


# ─── 4) no_token → App reinstall required (no OAuth fallback) ────
def test_no_token_requires_app_reinstall():
    result = _run(_make_db(), _FakeClient([]), token=None, error="no_token")
    assert result["ok"] is False
    assert result["reason"] == "app_reinstall_required"


# ─── 5) Cooldown blocks back-to-back heals ────────────────────────
def test_cooldown_blocks_repeat():
    from services import repo_heal as rh
    rh._last_heal_at["p1"] = time.time() - 10
    from services.repo_heal import heal_project
    result = asyncio.run(heal_project(
        db=_make_db(), user_id="u1", project_id="p1",
        prior_status={"error": "github_rejected", "auth": "app"},
    ))
    assert result["heal_attempted"] is False
    assert result["reason"] == "cooldown"


# ─── 6) Success invalidates the connection-status cache ───────────
def test_success_invalidates_status_cache():
    from routers import repo_status as rs
    rs._CACHE["p1"] = {"project_id": "p1", "status": "disconnected",
                       "checked_at": time.time()}
    client = _FakeClient([(lambda u, h: True, _Resp(200))])
    result = _run(_make_db(), client, error="github_rejected")
    assert result["ok"] is True
    assert "p1" not in rs._CACHE
