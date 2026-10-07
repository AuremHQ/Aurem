"""Regression — admin_gateway must carry a router-level admin gate.

Guard 21 (`check_admin_router_gates`) failed on main because
`routers/admin_gateway.py` gated per handler only, so a route added later
without the inline `require_admin` call would have shipped unprotected.
"""
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cto_services.auth import require_admin_dep
from routers.admin_gateway import router
from scripts.g21_security_scan import check_admin_router_gates


def test_regression_iter391_admin_gateway_has_router_level_admin_dependency():
    assert any(d.dependency is require_admin_dep for d in router.dependencies)
    assert "admin_gateway.py" not in check_admin_router_gates()


@pytest.mark.parametrize(
    "route",
    [r for r in router.routes if isinstance(r, APIRoute)],
    ids=lambda r: f"{min(r.methods)} {r.path}",
)
def test_regression_iter391_admin_gateway_rejects_unauthenticated_requests(route):
    app = FastAPI()
    app.include_router(router)
    path = route.path.replace("{key_id}", "x").replace("{client_id}", "x")
    resp = TestClient(app).request(min(route.methods), path)
    assert resp.status_code in (401, 403), (route.path, resp.status_code)
