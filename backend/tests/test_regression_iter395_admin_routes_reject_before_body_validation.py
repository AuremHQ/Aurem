"""Regression — admin routes must authenticate BEFORE the body is parsed.

Found in the 2026-09-29 admin audit: thinking-hints, suggestions
decide and self-bugs mark-fixed / review-proposal checked the admin token
inside the handler, so an unauthenticated request with an invalid body got
422 (validation ran first) instead of 401. Nothing was exposed, but the
auth check sat where a later edit could drop it unnoticed, and it sat
outside the router-level gate Guard 21 enforces.

The route/router-level `Depends(require_admin_dep)` now runs first.
"""
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cto_services.auth import require_admin_dep
from routers import self_bugs_admin, suggestions, thinking_hints


def _admin_routes():
    out = []
    for mod, keep in (
        (thinking_hints, lambda p: p.startswith("/admin/")),
        (suggestions, lambda p: p.startswith("/suggestions/admin/")),
        (self_bugs_admin, lambda p: p.startswith("/admin/self-bugs")),
    ):
        for r in mod.router.routes:
            if isinstance(r, APIRoute) and keep(r.path):
                out.append((mod, r))
    return out


ROUTES = _admin_routes()
IDS = [f"{min(r.methods)} {r.path}" for _, r in ROUTES]


def _gated(mod, route):
    deps = [d.dependency for d in route.dependencies] + [
        d.dependency for d in mod.router.dependencies
    ]
    return require_admin_dep in deps


def test_regression_iter395_found_the_expected_admin_routes():
    # Guards the parametrization itself: if a router is renamed the
    # tests below must not silently run on zero routes.
    assert len(ROUTES) >= 11


@pytest.mark.parametrize("mod,route", ROUTES, ids=IDS)
def test_regression_iter395_admin_route_carries_admin_dependency(mod, route):
    assert _gated(mod, route), f"{min(route.methods)} {route.path} has no require_admin_dep"


@pytest.mark.parametrize("mod,route", ROUTES, ids=IDS)
def test_regression_iter395_unauthenticated_invalid_body_gets_401_not_422(mod, route):
    app = FastAPI()
    app.include_router(mod.router)
    path = route.path
    for name in route.param_convertors:
        path = path.replace("{" + name + "}", "x")
    method = min(route.methods)
    kwargs = {"json": {"unexpected": "shape"}} if method in {"POST", "PUT", "PATCH"} else {}
    resp = TestClient(app, raise_server_exceptions=False).request(method, path, **kwargs)
    assert resp.status_code in (401, 403), (method, route.path, resp.status_code)
