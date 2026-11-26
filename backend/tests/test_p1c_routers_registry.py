"""L8 split tests — backend/routers_registry.py (moved out of main.py).

Proves the pure move preserved the route table exactly:
  * AST: the registry's 120 include_router calls equal the golden list
    (router name + prefix, in order) taken from main.py at 097697ba.
  * AST: main.py mounts nothing itself and delegates via exactly one
    register_routers(app) call; the split imports (mcp_discovery_root,
    api_catalog_root, oauth *_root) stayed in main.py.
  * Runtime: with stub router modules, register_routers() on a real
    FastAPI app mounts the same 120 routes in the same order, with the
    same prefixes — including ask_router twice — and the Iter 309
    SSE-probe try/except path (probe module absent) degrades gracefully.

Run from backend/: python -m pytest tests/test_p1c_routers_registry.py
"""
import ast
import importlib
import sys
import types
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

GOLDEN_MOUNTS = [
    ('deploy_router', '/api/aurem-dev'),
    ('architect_router', '/api/aurem-dev'),
    ('vault_router', '/api/aurem-dev'),
    ('stacks_router', '/api/aurem-dev'),
    ('domain_router', '/api/aurem-dev'),
    ('github_router', '/api/aurem-dev'),
    ('harden_router', '/api/aurem-dev'),
    ('trust_router', '/api/aurem-dev'),
    ('chat_commits_router', '/api/aurem-dev'),
    ('engagement_router', '/api/aurem-dev'),
    ('unlock_router', '/api/aurem-dev'),
    ('auth_router', '/api/aurem-dev'),
    ('mfa_router', '/api/aurem-dev'),
    ('chat_router', '/api/aurem-dev'),
    ('dev_tools_router', '/api/aurem-dev'),
    ('github_oauth_router', '/api/aurem-dev'),
    ('google_oauth_router', '/api/aurem-dev'),
    ('github_funnel_router', '/api/aurem-dev'),
    ('github_app_router', '/api/aurem-dev'),
    ('cto_projects_router', '/api/aurem-dev'),
    ('team_management_router', '/api/aurem-dev'),
    ('automations_router', '/api/aurem-dev'),
    ('upload_router', '/api/aurem-dev'),
    ('migrations_admin_router', '/api/aurem-dev'),
    ('backups_admin_router', '/api/aurem-dev'),
    ('admin_public_router', '/api/aurem-dev'),
    ('admin_qa_router', '/api/aurem-dev'),
    ('admin_health_router', '/api'),
    ('maintenance_public_router', '/api'),
    ('admin_maintenance_router', '/api'),
    ('synthetic_checks_ci_router', '/api/aurem-dev'),
    ('admin_observability_router', '/api'),
    ('admin_payments_router', '/api/aurem-dev'),
    ('admin_support_router', '/api/aurem-dev'),
    ('admin_users_router', '/api/aurem-dev'),
    ('admin_funnel_router', '/api/aurem-dev'),
    ('admin_pricing_router', '/api/aurem-dev'),
    ('admin_rea_router', '/api/aurem-dev'),
    ('admin_projects_brain_router', '/api/aurem-dev'),
    ('admin_ops_config_router', '/api/aurem-dev'),
    ('admin_llm_config_router', '/api/aurem-dev'),
    ('admin_llm_usd_cap_router', '/api/aurem-dev'),
    ('admin_ora_chat_limits_router', '/api/aurem-dev'),
    ('admin_video_pipeline_router', '/api/aurem-dev'),
    ('notifications_bell_router', '/api/aurem-dev'),
    ('admin_analytics_router', '/api/aurem-dev'),
    ('admin_bi_router', '/api/aurem-dev'),
    ('admin_health_score_router', '/api/aurem-dev'),
    ('admin_deploy_readiness_router', '/api/aurem-dev'),
    ('admin_context_bridge_router', '/api/aurem-dev'),
    ('founder_summary_router', '/api/aurem-dev'),
    ('rollback_v2_router', '/api/aurem-dev'),
    ('github_auth_migration_router', '/api/aurem-dev'),
    ('support_router', '/api/aurem-dev'),
    ('payments_router', '/api/aurem-dev'),
    ('stripe_webhook_compat_router', '/api'),
    ('harness_router', '/api'),
    ('deploy_share_router', '/api'),
    ('ask_router', None),
    ('ask_router', '/api'),
    ('harness_downloads_router', '/api/aurem-dev'),
    ('admin_harness_router', '/api/aurem-dev'),
    ('harness_telemetry_router', '/api'),
    ('usage_router', '/api/aurem-dev'),
    ('lint_preview_router', '/api/aurem-dev'),
    ('shipwall_router', '/api/aurem-dev'),
    ('wrapped_router', '/api/aurem-dev'),
    ('hosted_deploy_router', '/api/aurem-dev'),
    ('codebase_router', '/api/aurem-dev'),
    ('scaffold_router', '/api/aurem-dev'),
    ('visual_bugfix_router', '/api/aurem-dev'),
    ('managed_db_router', '/api/aurem-dev'),
    ('supabase_router', '/api/aurem-dev'),
    ('personal_track_router', '/api/aurem-dev'),
    ('ora_chat_router', '/api/aurem-dev'),
    ('github_deploy_router', '/api/aurem-dev'),
    ('thinking_hints_router', '/api/aurem-dev'),
    ('repo_indexing_router', '/api/aurem-dev'),
    ('founder_offer_router', '/api/aurem-dev'),
    ('onboarding_first_scan_router', '/api/aurem-dev'),
    ('onboarding_router', '/api/aurem-dev'),
    ('auth_verify_router', '/api/aurem-dev'),
    ('email_unsubscribe_router', '/api/aurem-dev'),
    ('admin_vanguard_router', '/api/aurem-dev'),
    ('admin_bin_router', '/api/aurem-dev'),
    ('admin_team_router', '/api/aurem-dev'),
    ('team_invite_accept_router', '/api/aurem-dev'),
    ('security_scan_router', '/api/aurem-dev'),
    ('vanguard_ci_router', '/api/aurem-dev'),
    ('fix_pipeline_router', '/api/aurem-dev'),
    ('project_versions_router', '/api/aurem-dev'),
    ('repo_status_router', '/api/aurem-dev'),
    ('codebase_health_router', '/api/aurem-dev'),
    ('evidence_router', '/api/aurem-dev'),
    ('verification_router', '/api/aurem-dev'),
    ('findings_router', '/api/aurem-dev'),
    ('suggestions_router', '/api/aurem-dev'),
    ('self_bugs_admin_router', '/api/aurem-dev'),
    ('admin_connect_diagnostic_router', '/api/aurem-dev'),
    ('admin_onboarding_funnel_router', '/api/aurem-dev'),
    ('version_router', None),
    ('advisor_ctx_router', None),
    ('project_memory_router', None),
    ('brain_admin_router', None),
    ('brain_user_router', None),
    ('qa_probe_router', '/api/aurem-dev'),
    ('notify_interest_router', '/api/aurem-dev'),
    ('integrity_log_router', '/api/aurem-dev'),
    ('loop_router', '/api/aurem-dev'),
    ('user_rollback_router', '/api/aurem-dev'),
    ('_iter309_probe_router', '/api'),
    ('trust_level_router', None),
    ('diagram_router', '/api/aurem-dev'),
    ('feature_window_router', '/api/aurem-dev'),
    ('mcp_router', '/api/aurem-dev'),
    ('mcp_ora_chat_router', '/api/aurem-dev'),
    ('admin_gateway_router', '/api/aurem-dev'),
    ('vercel_router', '/api/aurem-dev'),
    ('oauth_router', '/api/aurem-dev'),
    ('visibility_router', '/api/aurem-dev'),
]

REGISTRY = BACKEND / "routers_registry.py"
MAIN = BACKEND / "main.py"


def _mounts_of(path):
    tree = ast.parse(Path(path).read_text())
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"):
            # ast.walk is breadth-first: sort by source line so the order
            # is the mount order (matters: the SSE-probe mount sits inside
            # a try/except and would otherwise sort last).
            out.append((node.lineno, node.args[0].id,
                        {k.arg: ast.literal_eval(k.value) for k in node.keywords}))
    return [(name, kw) for _, name, kw in sorted(out)]


def _imports_of(path):
    """(module, imported_name, asname) for every ImportFrom in the file."""
    tree = ast.parse(Path(path).read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                out.append((node.module, a.name, a.asname or a.name))
    return out


def test_registry_mounts_match_golden():
    mounts = _mounts_of(REGISTRY)
    assert len(mounts) == 120, f"expected 120 mounts, got {len(mounts)}"
    got = [(name, kw.get("prefix")) for name, kw in mounts]
    assert got == GOLDEN_MOUNTS
    # only kwarg ever passed is prefix
    assert all(set(kw) <= {"prefix"} for _, kw in mounts)


def test_main_delegates_and_mounts_nothing():
    assert _mounts_of(MAIN) == []
    tree = ast.parse(MAIN.read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "register_routers"]
    assert len(calls) == 1
    assert isinstance(calls[0].args[0], ast.Name) and calls[0].args[0].id == "app"
    # the split imports stayed in main.py (used by /.well-known add_route)
    src = MAIN.read_text()
    for name in ("mcp_discovery_root", "api_catalog_root",
                 "oauth_discovery_root", "oauth_protected_resource_root",
                 "openid_configuration_root"):
        assert name in src, f"{name} missing from main.py"
    assert "oauth_router" not in src, "oauth_router must live in the registry, not main.py"


def test_every_imported_router_is_mounted_and_vice_versa():
    imports = _imports_of(REGISTRY)
    bound = {asname for _, _, asname in imports}
    mounted = {name for name, _ in GOLDEN_MOUNTS}
    assert bound == mounted, (
        f"imported-not-mounted={bound - mounted} mounted-not-imported={mounted - bound}")
    assert len(mounted) == 119  # ask_router is mounted twice
    assert [n for n, _ in GOLDEN_MOUNTS].count("ask_router") == 2


def _install_router_stubs(include_probe):
    """Stub every router module the registry imports.

    Each stub router is a real APIRouter with one marker route whose path
    encodes the bound name, so the mounted route paths prove order +
    prefix application. Returns (created_module_names, probe_stubbed).
    """
    created = []
    routers_pkg = types.ModuleType("routers")
    routers_pkg.__path__ = []
    sys.modules.setdefault("routers", routers_pkg)
    created.append("routers")
    by_module = {}
    for module, attr, asname in _imports_of(REGISTRY):
        by_module.setdefault(module, []).append((attr, asname))
    for module, attrs in by_module.items():
        if module == "routers.dev_sse_probe" and not include_probe:
            continue  # exercise the try/except fallback in register_routers
        if module == "services.codebase_indexer":
            mod = types.ModuleType(module)
        else:
            mod = types.ModuleType(module)
        for attr, asname in attrs:
            router = APIRouter()
            router.add_api_route(f"/__stub__/{asname}", lambda: {}, methods=["GET"])
            setattr(mod, attr, router)
        sys.modules[module] = mod
        created.append(module)
    return created


def _remove_modules(names):
    for name in names:
        sys.modules.pop(name, None)
    sys.modules.pop("routers_registry", None)


def _expected_paths():
    return [(prefix or "") + f"/__stub__/{name}" for name, prefix in GOLDEN_MOUNTS]


def _mounted_routes(app):
    """Mounted stub routes as (path, methods) pairs, in mount order.

    FastAPI >= 0.165 mounts lazily: app.routes holds _IncludedRouter
    markers whose effective_candidates() carry the prefix-applied paths.
    Older FastAPI mounts eagerly as plain Route objects. Both shapes are
    handled; built-in routes (/openapi.json, /docs, ...) never contain
    the __stub__ marker so they are filtered out either way.
    """
    out = []
    for route in app.routes:
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            out.extend((ctx.path, set(ctx.methods)) for ctx in candidates())
        elif hasattr(route, "path"):
            out.append((route.path, set(getattr(route, "methods", set()))))
    return [(p, m) for p, m in out if "__stub__" in p]


@pytest.mark.parametrize("include_probe", [False, True])
def test_register_routers_mounts_identical_routes_runtime(include_probe):
    created = _install_router_stubs(include_probe)
    try:
        import routers_registry
        importlib.reload(routers_registry)
        app = FastAPI()
        routers_registry.register_routers(app)
        mounted = _mounted_routes(app)
        paths = [p for p, _ in mounted]
        expected = _expected_paths()
        if not include_probe:
            # probe mount is skipped (module absent) — one fewer route
            probe_idx = next(i for i, (n, p) in enumerate(GOLDEN_MOUNTS)
                             if n == "_iter309_probe_router")
            expected = expected[:probe_idx] + expected[probe_idx + 1:]
        assert paths == expected
        assert len(paths) == (120 if include_probe else 119)
        # ask_router mounted twice, at its two golden positions — bare
        # (NLWeb /ask) and under /api (nginx only proxies /api/*)
        ask_positions = [i for i, (n, _) in enumerate(GOLDEN_MOUNTS)
                         if n == "ask_router"]
        assert len(ask_positions) == 2
        assert paths.count("/__stub__/ask_router") == 1
        assert paths.count("/api/__stub__/ask_router") == 1
        assert paths[ask_positions[0]] == "/__stub__/ask_router"
        assert paths[ask_positions[1]] == "/api/__stub__/ask_router"
        # every mounted stub route is the GET marker route
        assert all(methods == {"GET"} for _, methods in mounted)
    finally:
        _remove_modules(created)
