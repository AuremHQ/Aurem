"""tests/test_regression_iter2026_69_csp_twin_and_docs_gate.py

Security audit Phase 4.2 + 4.4.

4.2 CSP: the main security-headers middleware sets a Content-Security-Policy,
but its cached-response twin `_apply_security_headers` set NONE, so route-cache
hits went out without a CSP. Both now take the same constant. Only two safe
directives are added (object-src 'none', frame-ancestors 'none'); 'unsafe-inline'
is NOT removed (inline loaders in frontend/index.html would break).

4.4 Docs gating: /api/docs, /api/redoc, /api/openapi.json are listed for AI
agents, so they stay public. A new flag API_DOCS_REQUIRE_ADMIN (default OFF)
puts them behind the existing admin bearer check; nothing changes until it is set.
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "ci-test-secret-32-chars-minimum!!")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aurem_test")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

DOC_PATHS = ["/api/docs", "/api/redoc", "/api/openapi.json"]


def _directives(csp: str) -> dict[str, str]:
    out = {}
    for part in csp.split(";"):
        part = part.strip()
        if part:
            name, _, rest = part.partition(" ")
            out[name] = rest
    return out


# ── 4.2 CSP ───────────────────────────────────────────────────────────
def test_cached_twin_sets_the_same_csp_as_the_main_middleware():
    resp = Response("x")
    main._apply_security_headers(resp)
    assert resp.headers["Content-Security-Policy"] == main._CONTENT_SECURITY_POLICY


def test_live_response_carries_the_same_constant():
    client = TestClient(main.app, raise_server_exceptions=False)
    r = client.get("/api/__no_such_route__")   # a 404 still passes through the middleware; needs no DB
    assert r.headers.get("Content-Security-Policy") == main._CONTENT_SECURITY_POLICY


def test_both_new_directives_are_present_and_strict():
    d = _directives(main._CONTENT_SECURITY_POLICY)
    assert d["object-src"] == "'none'"
    assert d["frame-ancestors"] == "'none'"


def test_existing_directives_are_untouched_and_unsafe_inline_is_kept():
    d = _directives(main._CONTENT_SECURITY_POLICY)
    assert d["default-src"] == "'self'"
    assert "'unsafe-inline'" in d["script-src"] and "'unsafe-inline'" in d["style-src"]   # K4: not removed here
    assert d["script-src"] == "'self' 'unsafe-inline' https://auremcto.com https://bzrcdn.openai.com"
    assert d["style-src"] == "'self' 'unsafe-inline'"
    assert d["img-src"] == "'self' data: https:"
    assert d["frame-src"] == "'self' blob:" and d["font-src"] == "'self' data:"
    assert "https://api.github.com" in d["connect-src"] and "wss://auremcto.com" in d["connect-src"]


def test_frame_ancestors_matches_x_frame_options_deny():
    resp = Response("x")
    main._apply_security_headers(resp)
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]


def test_csp_has_no_wildcard_script_source():
    d = _directives(main._CONTENT_SECURITY_POLICY)
    assert "*" not in d["script-src"].split() and "'unsafe-eval'" not in d["script-src"]


# ── 4.4 docs gate ─────────────────────────────────────────────────────
@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_stay_public_when_the_flag_is_off_by_default(client, monkeypatch, path):
    monkeypatch.delenv("API_DOCS_REQUIRE_ADMIN", raising=False)
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no", "False", "  "])
@pytest.mark.parametrize("path", DOC_PATHS)
def test_falsy_flag_values_keep_docs_public(client, monkeypatch, path, value):
    monkeypatch.setenv("API_DOCS_REQUIRE_ADMIN", value)
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
@pytest.mark.parametrize("path", DOC_PATHS)
def test_truthy_flag_blocks_anonymous_callers(client, monkeypatch, path, value):
    monkeypatch.setenv("API_DOCS_REQUIRE_ADMIN", value)
    r = client.get(path)
    assert r.status_code in (401, 403)
    assert "openapi" not in r.text.lower() and "swagger" not in r.text.lower()


@pytest.mark.parametrize("path", DOC_PATHS)
def test_flag_on_non_admin_gets_403_and_admin_gets_docs(client, monkeypatch, path):
    monkeypatch.setenv("API_DOCS_REQUIRE_ADMIN", "1")

    async def deny(authorization, request=None):
        raise HTTPException(403, "Admin access required")

    monkeypatch.setattr(main, "_docs_require_admin", deny)
    assert client.get(path, headers={"Authorization": "Bearer user"}).status_code == 403

    async def allow(authorization, request=None):
        return {"user_id": "admin"}

    monkeypatch.setattr(main, "_docs_require_admin", allow)
    assert client.get(path, headers={"Authorization": "Bearer admin"}).status_code == 200


def test_flag_on_does_not_gate_other_api_routes(client, monkeypatch):
    monkeypatch.setenv("API_DOCS_REQUIRE_ADMIN", "1")
    assert client.get("/api/__no_such_route__").status_code == 404   # not 401/403: the gate only covers the docs paths


@pytest.mark.parametrize("path", ["/api/docs/", "/api/DOCS", "/api/docs/../docs", "/api/openapi.json/", "//api/docs",
                                  "/api/docs?x=1", "/api/openapi.json?cachebust=1"])
def test_path_tricks_do_not_slip_past_the_gate(client, monkeypatch, path):
    monkeypatch.setenv("API_DOCS_REQUIRE_ADMIN", "1")
    r = client.get(path)
    if path.split("?")[0].rstrip("/").lower().endswith(("/api/docs", "/api/openapi.json")) and "DOCS" not in path:
        assert r.status_code in (401, 403, 404, 307, 308)
        assert "swagger" not in r.text.lower() and '"openapi"' not in r.text.lower()


def test_gate_failure_is_a_generic_error_not_a_stack_trace(client, monkeypatch):
    monkeypatch.setenv("API_DOCS_REQUIRE_ADMIN", "1")

    async def boom(authorization, request=None):
        raise RuntimeError("db password is hunter2 at /app/secret.py")

    monkeypatch.setattr(main, "_docs_require_admin", boom)
    r = client.get("/api/docs")
    assert r.status_code in (401, 403, 500, 503)
    assert "hunter2" not in r.text and "/app/" not in r.text and "Traceback" not in r.text
