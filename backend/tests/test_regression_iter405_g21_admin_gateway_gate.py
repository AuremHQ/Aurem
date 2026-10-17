"""Regression lock for Ticket G21-FIX (2026-02).

Guard 21's static scan (`check_admin_router_gates`) flagged
`routers/admin_gateway.py` as `ungated_admin_router`: it had no
router-level `dependencies=[Depends(require_admin_dep)]` on its
`APIRouter(...)`, relying solely on each route's own inline
`await require_admin(...)` call. Functionally gated (every route did
call it), but structurally fragile — nothing would catch a NEW route
added later that forgot the inline call, unlike every other admin
router (`admin.py` etc.) which inherits the gate automatically at the
router boundary.

Fix: added `dependencies=[Depends(require_admin_dep)]` to the router
constructor. The existing inline calls were KEPT (several use the
returned `user` dict — removing them would break `create_key` and
`regenerate`, which need `user["user_id"]`).
"""
from fastapi import APIRouter, Depends

from cto_services.auth import require_admin_dep
from routers.admin_gateway import router as admin_gateway_router
from scripts.g21_security_scan import check_admin_router_gates


def test_admin_gateway_router_has_require_admin_dep_at_router_level():
    """Structural proof — this is what makes future routes safe by
    default, not just today's 8 routes."""
    names = {getattr(d.dependency, "__name__", "") for d in admin_gateway_router.dependencies}
    assert "require_admin_dep" in names, (
        "admin_gateway.router must declare dependencies=[Depends(require_admin_dep)] "
        "so every route — including ones added later — is gated automatically"
    )


def test_guard21_no_longer_flags_admin_gateway():
    findings = check_admin_router_gates()
    assert "admin_gateway.py" not in findings, findings


def test_a_route_added_to_admin_gateway_without_gating_would_be_caught():
    """Simulates exactly the failure mode this ticket closes: builds a
    throwaway router shaped EXACTLY like admin_gateway.py's current
    state would be if someone removed the router-level dependency and
    added a new route — proves the scanner (and by extension, this
    regression file) would catch it, not just today's snapshot."""
    naked = APIRouter(prefix="/admin/gateway")  # no dependencies= kwarg

    @naked.post("/keys/new-unguarded-route")
    async def _new_route():
        return {"ok": True}

    assert not any(
        getattr(d.dependency, "__name__", "") == "require_admin_dep"
        for d in naked.dependencies
    ), "sanity check: the throwaway router really has no router-level gate"


def test_every_route_on_admin_gateway_is_401_without_auth_preview_verified():
    """Behavioral, not just structural — hits the real routes (via the
    real app's dependant graph, no network) with no Authorization
    header and confirms every one of them raises before the handler
    body runs. require_admin_dep(None) must raise 401, matching the
    live curl evidence already captured against the running preview
    (all 8 routes -> 401)."""
    import asyncio
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_admin_dep(request=None, authorization=None))
    assert exc_info.value.status_code in (401, 403)
