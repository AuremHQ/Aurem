"""Admin Team S1 (AT1a) — fitness test.

Walks the REAL FastAPI app routes (not a hand-maintained file list) and
fails if any route that reaches an admin check — via a router-level
`require_admin_dep` dependency OR an inline `_require_admin`/`require_admin`
call inside the handler body (detected by bytecode inspection, per D20:
"any route whose handler or dependencies reach any admin check, in any
file, not only routes in admin_*.py") — has no entry in
`services.admin_access_registry.ADMIN_ROUTE_REGISTRY`.
"""
from fastapi.routing import APIRoute
from server import app
from cto_services.auth import require_admin_dep, require_admin
from services.admin_access_registry import ADMIN_ROUTE_REGISTRY

import re

_PARAM_RE = re.compile(r"\{[^{}]+\}")


def _normalize(path: str) -> str:
    """Collapse any `{param_name}` segment to `{*}` so a registry entry
    written with one param name still matches the real route even if the
    handler's actual parameter name differs (e.g. `{user_id}` vs `{bin_id}`)."""
    return _PARAM_RE.sub("{*}", path)


_REGISTRY_KEYS = {(m, _normalize(p)) for (m, p, _key, _level) in ADMIN_ROUTE_REGISTRY}

_ADMIN_CHECK_NAMES = {"_require_admin", "require_admin"}


def _route_has_admin_dependency(route) -> bool:
    for dep in getattr(route.dependant, "dependencies", []):
        call = getattr(dep, "call", None)
        name = getattr(call, "__name__", "")
        if name in {"require_admin_dep", "require_admin"}:
            return True
    return False


def _route_handler_calls_admin_check(route) -> bool:
    endpoint = route.endpoint
    code = getattr(endpoint, "__code__", None)
    if code is None:
        return False
    return bool(_ADMIN_CHECK_NAMES & set(code.co_names))


def _admin_gated_routes():
    out = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if _route_has_admin_dependency(route) or _route_handler_calls_admin_check(route):
            for method in route.methods:
                if method == "HEAD":
                    continue
                out.append((method, route.path, route))
    return out


def test_regression_iter_adminteam_s1_fitness_every_admin_route_is_registered():
    gated = _admin_gated_routes()
    missing = sorted({
        (method, _normalize(path)) for (method, path, _route) in gated
        if (method, _normalize(path)) not in _REGISTRY_KEYS
    })
    assert not missing, (
        f"{len(missing)} admin-gated route(s) have no registry entry in "
        f"services/admin_access_registry.py: {missing}"
    )


def test_regression_iter_adminteam_s1_fitness_real_admin_route_count():
    gated = _admin_gated_routes()
    # Documents the real count found on this build, for the stage report —
    # not a strict equality (route count will grow over time), just a floor
    # proving the sweep actually found the bulk of what Q6/Q7 identified.
    assert len(gated) >= 150, f"expected at least 150 admin-gated routes, found {len(gated)}"
