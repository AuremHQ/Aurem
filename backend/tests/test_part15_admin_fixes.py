"""tests/test_part15_admin_fixes.py — Part XV (ORA audit) fixes.

Proves the Part XV fixes in:
  services/route_cache.py          (3b: manual `invalidate()` purge)
  services/admin_analytics_cache.py (3a: bounded SWR staleness —
                                     `max_stale_seconds`, `_swr_staleness`)
  routers/admin_users.py           (3a: funnel passes max_stale_seconds)
  routers/admin_ops_config.py      (3b: POST /admin/cache/route-invalidate;
                                     purge_caches also clears route cache)
  services/admin_access_registry.py (2: settings owner-only intent made
                                     explicit; route-invalidate entry)
  routers/admin.py / admin_bin.py  (4: stale docstrings corrected;
                                     stub stays a stub — 0 routes)
  pages/AdminAuditPage.jsx         (1: 30 s polling — covered by the
                                     vitest file, not here)
  pages/Admin.jsx                  (4a: dead AuremAdminPanel import gone)
  components/ChatPanel.jsx         (nit: Loop Mode comment fixed)
  App.jsx                          (nit NOT applied: BrainDump takes no
                                     props, so `initialTab` cannot apply)

How it runs
-----------
The backend's full dependency tree is not installed in every
sandbox, so the modules under test are imported through lightweight
sys.modules stubs installed below (cto_services.* only — the REAL
module files are always loaded from this backend/ tree). Where the
real dependency is importable (fastapi, pydantic) the real one is
used. Run from anywhere:
    env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy \\
        -u https_proxy -u all_proxy -u NO_PROXY -u no_proxy \\
        part11/.venv/bin/python -m pytest backend/tests/test_part15_admin_fixes.py -v
(or use part15/run_tests.py, which also runs the neighbours).

Negative control: the same file runs against the pristine seed with
    PART15_BACKEND=<path to pristine/backend>
The new-behaviour tests (expired SWR, route-invalidate) fail there;
the no-change tests (registry entries, BrainDump props) pass there.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import types
from pathlib import Path

import pytest

BACKEND = Path(os.environ.get(
    "PART15_BACKEND", Path(__file__).resolve().parents[1]))
FRONTEND = BACKEND.parent / "frontend"


# ─── Import stubs (cto_services only; everything else is real) ─────

def _install_stubs() -> None:
    if "cto_services.auth" in sys.modules and "cto_services.db" in sys.modules:
        return
    sys.path.insert(0, str(BACKEND))

    async def _admin_user(_authorization=None, request=None):
        return {"user_id": "admin", "is_admin": True}

    def _require_admin_dep():
        return {"user_id": "admin", "is_admin": True}

    async def _finish_admin_check(user, request=None):
        return user

    def _get_db():
        return None

    def _require_db():
        raise RuntimeError("require_db stub — tests must patch it")

    cto = types.ModuleType("cto_services")
    cto.__path__ = []
    sys.modules.setdefault("cto_services", cto)
    auth = types.ModuleType("cto_services.auth")
    auth.current_dev = _admin_user
    auth.require_admin_dep = _require_admin_dep
    auth.require_admin = _admin_user
    auth._finish_admin_check = _finish_admin_check
    sys.modules.setdefault("cto_services.auth", auth)
    db = types.ModuleType("cto_services.db")
    db.get_db = _get_db
    db.require_db = _require_db
    sys.modules.setdefault("cto_services.db", db)

    services = types.ModuleType("services")
    services.__path__ = [str(BACKEND / "services")]
    sys.modules.setdefault("services", services)
    # Minimal tree has no services/http package (it exists in the real
    # repo); purge_caches imports it at call time, outside try/except,
    # but only USES it when Cloudflare env vars are set — which tests
    # never set. A dummy import satisfies the import without faking
    # any behaviour.
    http_mod = types.ModuleType("services.http")

    def _ext_client_unused(*args, **kwargs):
        raise RuntimeError("services.http stub — not used by these tests")

    http_mod.ext_client = _ext_client_unused
    sys.modules.setdefault("services.http", http_mod)
    routers = types.ModuleType("routers")
    routers.__path__ = [str(BACKEND / "routers")]
    sys.modules.setdefault("routers", routers)


_install_stubs()

import services.admin_analytics_cache as swr_mod  # noqa: E402
import services.route_cache as route_cache  # noqa: E402
from services.admin_access_registry import ADMIN_ROUTE_REGISTRY  # noqa: E402


# ─── Fake Mongo (for mongo_swr_cache) ───────────────────────────────

class _FakeColl:
    """Minimal async Mongo collection fake: find_one / update_one ($set)."""

    def __init__(self, doc=None):
        self.doc = doc
        self.update_calls: list = []

    async def find_one(self, query, projection=None):
        return self.doc

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append((query, update))
        base = dict(self.doc or {})
        base.update(update.get("$set", {}))
        base["_id"] = query.get("_id", base.get("_id"))
        self.doc = base
        return types.SimpleNamespace(modified_count=1, upserted_id=None)


class _FakeDB:
    def __init__(self, coll):
        self._coll = coll

    def __getitem__(self, name):
        return self._coll


@pytest.fixture
def _swr_db(monkeypatch):
    """Install a fake get_db returning a per-test fake collection.

    Returns a helper to seed the SWR doc: _swr_db.seed(value, age_s).
    """
    coll = _FakeColl(None)
    db = _FakeDB(coll)
    monkeypatch.setattr(sys.modules["cto_services.db"], "get_db",
                        lambda: db)
    state = {"coll": coll}

    def seed(value, age_s, key="admin:activation_funnel:v1"):
        state["coll"].doc = {
            "_id": key,
            "value": value,
            "computed_at": time.time() - age_s,
        }
        return state["coll"]

    state["seed"] = seed
    return state


def _builder(value, calls, delay=0.0, fail=False):
    async def build():
        calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        if fail:
            raise RuntimeError("builder boom")
        return value
    return build


# ═══════════════════════════════════════════════════════════════════
# 3a — _swr_staleness (pure classification)
# ═══════════════════════════════════════════════════════════════════

def test_swr_staleness_fresh_within_ttl():
    assert swr_mod._swr_staleness(10, 300.0, 300.0) == "fresh"
    assert swr_mod._swr_staleness(300.0, 300.0, 300.0) == "fresh"


def test_swr_staleness_grace_window_is_swr():
    assert swr_mod._swr_staleness(301, 300.0, 300.0) == "swr"
    assert swr_mod._swr_staleness(600.0, 300.0, 300.0) == "swr"


def test_swr_staleness_past_grace_is_expired():
    assert swr_mod._swr_staleness(601, 300.0, 300.0) == "expired"
    assert swr_mod._swr_staleness(10_000, 300.0, 300.0) == "expired"


def test_swr_staleness_legacy_none_serves_stale_forever():
    # max_stale_seconds=None preserves the pre-Part-XV behaviour.
    assert swr_mod._swr_staleness(10_000, 300.0, None) == "swr"


def test_swr_staleness_zero_grace_expires_right_after_ttl():
    assert swr_mod._swr_staleness(300.0, 300.0, 0.0) == "fresh"
    assert swr_mod._swr_staleness(301, 300.0, 0.0) == "expired"


# ═══════════════════════════════════════════════════════════════════
# 3a — mongo_swr_cache behaviour (fake Mongo)
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_swr_fresh_doc_returned_builder_not_called(_swr_db):
    coll = _swr_db["seed"]({"ok": True, "n": 1}, age_s=60)
    calls = []
    res = await swr_mod.mongo_swr_cache(
        "admin:activation_funnel:v1", 300.0, _builder({"ok": True, "n": 2}, calls),
        max_stale_seconds=300.0)
    assert res == {"ok": True, "n": 1}
    assert calls == []


@pytest.mark.asyncio
async def test_swr_within_grace_returns_stale_and_refreshes(_swr_db):
    coll = _swr_db["seed"]({"ok": True, "n": 1}, age_s=400)
    calls = []
    res = await swr_mod.mongo_swr_cache(
        "admin:activation_funnel:v1", 300.0, _builder({"ok": True, "n": 2}, calls),
        max_stale_seconds=300.0)
    assert res == {"ok": True, "n": 1}  # stale served (within grace)
    await asyncio.sleep(0.1)  # let the background refresh land
    assert calls == [1]
    assert coll.doc["value"] == {"ok": True, "n": 2}


@pytest.mark.asyncio
async def test_swr_past_grace_recomputes_instead_of_serving_stale(_swr_db):
    """THE fix: a funnel read past TTL+grace does NOT return stale data."""
    coll = _swr_db["seed"]({"ok": True, "n": 1}, age_s=3600)
    calls = []
    res = await swr_mod.mongo_swr_cache(
        "admin:activation_funnel:v1", 300.0, _builder({"ok": True, "n": 2}, calls),
        max_stale_seconds=300.0)
    assert res == {"ok": True, "n": 2}  # fresh, recomputed synchronously
    assert calls == [1]
    assert res != {"ok": True, "n": 1}


@pytest.mark.asyncio
async def test_swr_past_grace_builder_fails_returns_stale_marker(_swr_db):
    coll = _swr_db["seed"]({"ok": True, "n": 1}, age_s=3600)
    calls = []
    res = await swr_mod.mongo_swr_cache(
        "admin:activation_funnel:v1", 300.0, _builder(None, calls, fail=True),
        max_stale_seconds=300.0, hard_timeout=4.0)
    # Explicit marker — never the stale value presented as current.
    assert res.get("_status") == "stale"
    assert "ok" not in res  # marker carries no funnel fields
    assert "_stale_age_seconds" in res
    assert res["_stale_age_seconds"] >= 3600
    assert res != {"ok": True, "n": 1}
    await asyncio.sleep(0.1)  # background refresh attempt also fails; must not raise


@pytest.mark.asyncio
async def test_swr_past_grace_slow_builder_times_out_to_marker(_swr_db):
    coll = _swr_db["seed"]({"ok": True, "n": 1}, age_s=3600)
    calls = []
    res = await swr_mod.mongo_swr_cache(
        "admin:activation_funnel:v1", 300.0,
        _builder({"ok": True, "n": 2}, calls, delay=0.5),
        max_stale_seconds=300.0, hard_timeout=0.05)
    assert res.get("_status") == "stale"
    assert res != {"ok": True, "n": 1}
    await asyncio.sleep(0.6)  # let the background recompute finish quietly


@pytest.mark.asyncio
async def test_swr_legacy_none_still_serves_very_stale(_swr_db):
    """Back-compat: without max_stale_seconds the old SWR behaviour
    (serve stale indefinitely) is preserved for other callers."""
    coll = _swr_db["seed"]({"ok": True, "n": 1}, age_s=100_000)
    calls = []
    res = await swr_mod.mongo_swr_cache(
        "admin:activation_funnel:v1", 300.0, _builder({"ok": True, "n": 2}, calls))
    assert res == {"ok": True, "n": 1}
    await asyncio.sleep(0.1)


def test_funnel_route_passes_bounded_staleness():
    """The live funnel route opts into the bound (300 s grace)."""
    text = (BACKEND / "routers" / "admin_users.py").read_text()
    idx = text.index('key="admin:activation_funnel:v1"')
    block = text[max(0, idx - 1200): idx + 600]
    assert "max_stale_seconds=300.0" in block
    assert "mongo_swr_cache(" in block


# ═══════════════════════════════════════════════════════════════════
# 3b — route_cache.invalidate + the new endpoint / purge wiring
# ═══════════════════════════════════════════════════════════════════

def test_route_cache_invalidate_one_path_drops_all_variants():
    route_cache.clear()
    route_cache.put("/api/aurem-dev/admin/council/stats", 60, 200, b"v1", "application/json")
    route_cache.put(route_cache.make_key("/api/aurem-dev/admin/council/stats", "a=1&b=2"), 60, 200, b"v2", "application/json")
    route_cache.put("/api/aurem-dev/admin/mode-telemetry", 60, 200, b"t", "application/json")
    dropped = route_cache.invalidate("/api/aurem-dev/admin/council/stats")
    assert dropped == 2
    assert route_cache.get("/api/aurem-dev/admin/council/stats") is None
    assert route_cache.get(route_cache.make_key("/api/aurem-dev/admin/council/stats", "a=1&b=2")) is None
    # The other route is untouched — invalidation is scoped, not global.
    assert route_cache.get("/api/aurem-dev/admin/mode-telemetry") is not None
    route_cache.clear()


def test_route_cache_invalidate_all_and_missing():
    route_cache.clear()
    route_cache.put("/api/aurem-dev/admin/db-health", 30, 200, b"h", "application/json")
    assert route_cache.invalidate("/api/aurem-dev/admin/nope") == 0
    assert route_cache.get("/api/aurem-dev/admin/db-health") is not None
    assert route_cache.invalidate() == 1
    assert route_cache.get("/api/aurem-dev/admin/db-health") is None
    assert route_cache.invalidate() == 0
    route_cache.clear()


def test_route_cache_invalidate_refetch_roundtrip():
    """An invalidated entry refetches: put v1, invalidate, put v2, get v2."""
    route_cache.clear()
    key = "/api/aurem-dev/admin/council/stats"
    route_cache.put(key, 60, 200, b"stale-body", "application/json")
    assert route_cache.get(key)[1] == b"stale-body"
    route_cache.invalidate(key)
    assert route_cache.get(key) is None  # miss → next request refetches
    route_cache.put(key, 60, 200, b"fresh-body", "application/json")
    assert route_cache.get(key)[1] == b"fresh-body"
    route_cache.clear()


@pytest.mark.asyncio
async def test_route_invalidate_endpoint_drops_entry(monkeypatch):
    """POST /admin/cache/route-invalidate (router-level, real handler)."""
    import routers.admin_ops_config as ops
    route_cache.clear()
    key = "/api/aurem-dev/admin/council/stats"
    route_cache.put(key, 60, 200, b"v1", "application/json")
    res = await ops.admin_route_cache_invalidate(
        {"path": key}, authorization="Bearer x")
    assert res == {"ok": True, "dropped": 1, "path": key}
    assert route_cache.get(key) is None
    # Unknown path drops nothing but still succeeds.
    res = await ops.admin_route_cache_invalidate(
        {"path": "/api/aurem-dev/admin/nope"}, authorization="Bearer x")
    assert res == {"ok": True, "dropped": 0, "path": "/api/aurem-dev/admin/nope"}
    # Empty body purges everything.
    route_cache.put(key, 60, 200, b"v1", "application/json")
    res = await ops.admin_route_cache_invalidate({}, authorization="Bearer x")
    assert res == {"ok": True, "dropped": 1, "path": None}
    route_cache.clear()


@pytest.mark.asyncio
async def test_purge_caches_now_clears_route_cache(monkeypatch):
    """POST /admin/cache/purge reports the route cache in its report."""
    import routers.admin_ops_config as ops
    route_cache.clear()
    key = "/api/aurem-dev/admin/mode-telemetry"
    route_cache.put(key, 60, 200, b"v1", "application/json")
    res = await ops.purge_caches(authorization="Bearer x")
    assert res["ok"] is True
    assert res["report"]["route_cache"] == {"status": "ok", "dropped": 1}
    assert route_cache.get(key) is None
    route_cache.clear()


def test_route_invalidate_registry_entry():
    entry = next(
        (e for e in ADMIN_ROUTE_REGISTRY
         if e[0] == "POST"
         and e[1] == "/api/aurem-dev/admin/cache/route-invalidate"),
        None)
    assert entry is not None
    # Mirrors the other cache endpoints (purge / analytics-invalidate).
    assert entry[2] == "system.services"
    assert entry[3] == 2  # WORK


# ═══════════════════════════════════════════════════════════════════
# 2 — settings screen_key: not a defect, intent now explicit
# ═══════════════════════════════════════════════════════════════════

def test_settings_registry_entry_still_owner_only():
    """Behaviour UNCHANGED: composite settings stays key=None (hard 403
    for members) — only the comment explaining why is new."""
    by_path = {(m, p): (k, lvl) for m, p, k, lvl in ADMIN_ROUTE_REGISTRY}
    assert by_path[("GET", "/api/aurem-dev/admin/settings")] == (None, 1)  # VIEW
    assert by_path[("POST", "/api/aurem-dev/admin/settings")] == (None, "owner_only")


def test_settings_owner_only_intent_is_documented_both_sides():
    registry = (BACKEND / "services" / "admin_access_registry.py").read_text()
    assert "INTENTIONAL owner-only" in registry
    assert "screen_key is None" in registry or "screen_key None" in registry
    nav = (FRONTEND / "src" / "lib" / "adminNav.js").read_text()
    assert 'screenKeys: []' in nav
    # The frontend comment was already there; the registry now matches it.
    assert "intentionally" in nav
    settings_line = next(
        ln for ln in nav.splitlines()
        if '"settings"' in ln and "screenKeys" in ln)
    assert "screenKeys: []" in settings_line


def test_granular_settings_keys_untouched():
    by_path = {(m, p): (k, lvl) for m, p, k, lvl in ADMIN_ROUTE_REGISTRY}
    assert by_path[("GET", "/api/aurem-dev/admin/stripe-config")][0] == "settings.paysetup"
    assert by_path[("GET", "/api/aurem-dev/admin/llm/configs")][0] == "settings.models"
    assert by_path[("GET", "/api/aurem-dev/admin/github-app-config")][0] == "settings.githubapp"
    assert by_path[("GET", "/api/aurem-dev/admin/video/secrets")][0] == "settings.video"


# ═══════════════════════════════════════════════════════════════════
# 4 — stale docstrings corrected; stub stays a stub
# ═══════════════════════════════════════════════════════════════════

def test_admin_py_docstring_no_longer_claims_mounted():
    text = (BACKEND / "routers" / "admin.py").read_text()
    assert "Mounted under /api/aurem-dev/admin/* by main.py." not in text
    assert "HELPER-ONLY stub" in text
    assert "NEVER `include_router`'d" in text


def test_admin_bin_docstring_no_longer_claims_3680_loc():
    text = (BACKEND / "routers" / "admin_bin.py").read_text()
    assert "Existing admin.py (3680 LOC) is UNTOUCHED" not in text
    assert "HELPER-ONLY stub" in text


def test_stale_60s_cache_comments_corrected():
    """The '60 s TTL cache in admin.py' claim is gone from all three
    files that carried it; each now points at the real cache layers."""
    for rel in ("routers/admin.py", "routers/admin_users.py",
                "routers/admin_ops_config.py"):
        text = (BACKEND / rel).read_text()
        assert "Iter 212m-71 — 60 s TTL cache for the heavy admin aggregations" not in text
    admin_py = (BACKEND / "routers" / "admin.py").read_text()
    assert "this file has no cache" in admin_py
    assert "services/admin_analytics_cache.py" in admin_py
    # Dangling wrapper references fixed too.
    assert "so the cache wrapper above can call it" not in admin_py
    assert "Mongo SWR cache wrapper" in admin_py
    assert "no longer live in this file" in admin_py


def test_admin_py_still_zero_routes_and_helpers_live():
    import routers.admin as admin_mod
    assert len(admin_mod.router.routes) == 0
    for name in ("_compute_activation_funnel", "_compute_stage_users",
                 "_github_app_live_probe", "_bucket_label", "_run_skill",
                 "_email_hint", "_epoch", "_require_admin"):
        assert hasattr(admin_mod, name), f"helper {name} must stay importable"


def test_admin_py_not_mounted_by_main():
    # NOTE (folded tree): p1c's L8 split moved all 120 router mounts
    # out of main.py into routers_registry.py — so the live mount is
    # asserted there, and the dead admin.py import must be absent
    # from BOTH files.
    main = (BACKEND / "main.py").read_text()
    registry = (BACKEND / "routers_registry.py").read_text()
    assert "from routers.admin import" not in main
    assert "from routers.admin import" not in registry
    assert "admin_bin_router" in registry  # the live one is still mounted


def test_admin_jsx_dead_panel_import_removed():
    text = (FRONTEND / "src" / "pages" / "Admin.jsx").read_text()
    assert 'import AuremAdminPanel from' not in text
    assert 'from "../components/AuremAdminPanel"' not in text


# ═══════════════════════════════════════════════════════════════════
# Nits — ChatPanel comment; BrainDump initialTab not applicable
# ═══════════════════════════════════════════════════════════════════

def test_chatpanel_loop_comment_matches_pro_team_unlock():
    text = (FRONTEND / "src" / "components" / "ChatPanel.jsx").read_text()
    assert "locked to founder /\n  // admin / unlimited accounts" not in text
    assert "PLUS all Pro/Team tiers (unlocked 2026-08-21" in text
    # The code underneath is unchanged.
    assert 'u.tier === "pro" || u.tier === "team"' in text


def test_braindump_takes_no_props_so_no_initial_tab():
    """App.jsx /admin/brain/:projectId renders <BrainDump /> with no
    initialTab — correctly: BrainDump() takes no props and renders
    outside the Admin shell, so `initialTab` (an <Admin> prop) cannot
    apply. This pins the decision NOT to add a dead prop."""
    brain = (FRONTEND / "src" / "pages" / "BrainDump.jsx").read_text()
    assert "export default function BrainDump()" in brain
    app = (FRONTEND / "src" / "App.jsx").read_text()
    line = next(ln for ln in app.splitlines()
                if "/admin/brain/:projectId" in ln)
    assert "<BrainDump />" in line
    assert "initialTab" not in line


# ═══════════════════════════════════════════════════════════════════
# 1 — audit polling (source-level; behaviour proven by the vitest file)
# ═══════════════════════════════════════════════════════════════════

def test_audit_page_polls_both_tabs_at_30s():
    text = (FRONTEND / "src" / "pages" / "AdminAuditPage.jsx").read_text()
    assert "AUDIT_POLL_MS = 30000" in text
    assert text.count("setInterval(") == 2
    assert "clearInterval(t)" in text
    # Quiet polls (no loading flash); mount + manual refresh still load.
    assert "load(true)" in text
    assert "load(signalsOnlyRef.current, true)" in text
    # Manual Refresh buttons kept, both tabs.
    assert 'data-testid="admin-audit-actions-refresh"' in text
    assert 'data-testid="admin-audit-refresh"' in text
    # Turns poll pauses while the actions sub-tab is visible.
    assert 'if (sub !== "turns") return undefined;' in text
