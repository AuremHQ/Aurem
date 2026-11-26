"""
tests/test_part89_free_scan_mount.py — Part VIII audit fix:
routers/cto_projects/free_scan.py was the ONLY unmounted router with
live routes (GET /projects/{project_id}/free-scan); the frontend
FreeScanResults page called it and got a 404. The fix is one import
line in routers/cto_projects/__init__.py.

The existing tests import get_free_scan directly — which is exactly
why they passed while production 404'd. THIS test asserts the route
exists in app.routes after mounting the real package router, the way
main.py mounts it (prefix /api/aurem-dev + package prefix /cto).

Offline unit tests — the real package __init__ is executed with its
heavy siblings stubbed (management/brain/…/worker_api) so the test
does not need Mongo, GitHub, or the whole backend. Run from backend/
with the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part89_free_scan_mount.py -q
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_CTO_DIR = _BACKEND_DIR / "routers" / "cto_projects"

# Submodules of routers.cto_projects (all except free_scan, which is
# loaded for real). Each stub answers any attribute with a dummy so
# the package's compat re-exports keep resolving.
_STUB_SUBMODULES = (
    "management", "brain", "graph", "preview", "report_pdf",
    "what_changed", "worker_api", "worker_git", "rollback", "tasks",
    "github_browse",
)


def _attr_stub_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)

    def __getattr__(attr: str):  # noqa: N807 — module __getattr__ (PEP 562)
        async def _dummy(*args, **kwargs):
            return None
        return _dummy

    mod.__getattr__ = __getattr__
    return mod


class _PushFailedError(Exception):
    pass


@pytest.fixture()
def cto_package(monkeypatch):
    """Execute the REAL routers/cto_projects/__init__.py with stubbed
    heavy deps; yield the package module; restore sys.modules after."""
    saved = {}

    def _set(name, mod):
        saved[name] = sys.modules.get(name, None)
        sys.modules[name] = mod

    routers_pkg = types.ModuleType("routers")
    routers_pkg.__path__ = [str(_BACKEND_DIR / "routers")]
    _set("routers", routers_pkg)

    cto_services = types.ModuleType("cto_services")
    cto_services.__path__ = []
    _set("cto_services", cto_services)

    async def _current_dev(authorization=None):
        return {"user_id": "test-user"}

    _set("cto_services.auth", types.ModuleType("cto_services.auth"))
    sys.modules["cto_services.auth"].current_dev = _current_dev
    _set("cto_services.db", types.ModuleType("cto_services.db"))
    sys.modules["cto_services.db"].get_db = lambda: None
    sys.modules["cto_services.db"].require_db = lambda: None

    _set("core", types.ModuleType("core"))
    sys.modules["core"].__path__ = []
    _set("core.errors", types.ModuleType("core.errors"))
    sys.modules["core.errors"].PushFailedError = _PushFailedError

    # "services" stays a REAL package (real __path__) so the
    # agent-skills/mode-routing modules keep importing; only the
    # heavy leaves the cto __init__ needs are stubbed.
    if "services" not in sys.modules:
        services_pkg = types.ModuleType("services")
        services_pkg.__path__ = [str(_BACKEND_DIR / "services")]
        _set("services", services_pkg)
    for leaf in ("services.usage", "services.github_api_writer",
                 "services.cto_projects_helpers", "services.ambiguity_gate"):
        _set(leaf, _attr_stub_module(leaf))
    sys.modules["services.ambiguity_gate"].is_ambiguous_task = (
        lambda *a, **k: False)

    pkg = types.ModuleType("routers.cto_projects")
    pkg.__path__ = [str(_CTO_DIR)]
    _set("routers.cto_projects", pkg)
    for sub in _STUB_SUBMODULES:
        _set(f"routers.cto_projects.{sub}", _attr_stub_module(
            f"routers.cto_projects.{sub}"))

    spec = importlib.util.spec_from_file_location(
        "routers.cto_projects", _CTO_DIR / "__init__.py")
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(pkg)
    yield pkg
    for name, prev in saved.items():
        if prev is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prev
    for name in list(sys.modules):
        if name.startswith("routers.cto_projects"):
            sys.modules.pop(name, None)


def test_free_scan_import_present_in_package_init():
    """The one-line fix itself: free_scan must be imported alongside
    its siblings, or the decorator never runs."""
    text = (_CTO_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "from . import free_scan" in text


def test_free_scan_route_registered_on_package_router(cto_package):
    # The shared router carries the package prefix /cto (set in
    # __init__.py); the full mounted path is asserted in the next test.
    paths = [getattr(r, "path", None) for r in cto_package.router.routes]
    assert "/cto/projects/{project_id}/free-scan" in paths


def test_free_scan_route_present_in_app_routes(cto_package):
    """Mount-level: the exact URL the frontend calls resolves in the
    app once the package router is mounted the way main.py does it.
    (This FastAPI version defers include_router lazily — the route
    lives behind an _IncludedRouter in app.routes — so the proof is
    reverse-resolution, which exercises the same lazy matcher the
    server uses to serve the request.)"""
    app = FastAPI()
    app.include_router(cto_package.router, prefix="/api/aurem-dev")
    assert (str(app.url_path_for("get_free_scan", project_id="p1"))
            == "/api/aurem-dev/cto/projects/p1/free-scan")


def test_get_free_scan_importable_from_real_module(cto_package):
    """The endpoint function itself loads from the real module (not
    a stub) once the package import has run."""
    spec = importlib.util.spec_from_file_location(
        "routers.cto_projects.free_scan", _CTO_DIR / "free_scan.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["routers.cto_projects.free_scan"] = mod
    spec.loader.exec_module(mod)
    assert callable(mod.get_free_scan)
