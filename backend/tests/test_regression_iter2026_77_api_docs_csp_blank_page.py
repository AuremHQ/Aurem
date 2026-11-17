"""tests/test_regression_iter2026_77_api_docs_csp_blank_page.py

Real bug, found live: /api/docs (Swagger UI) and /api/redoc render a
blank white page in a real browser. curl returns 200 and the page's
own <title> loads fine, so this never showed up in a status-code
check — only in an actual browser, where the CSP's script-src never
allowed cdn.jsdelivr.net (where swagger-ui-dist ships its JS/CSS),
so the script was silently blocked, window.SwaggerUIBundle stayed
undefined, and #swagger-ui never rendered.

Fix: main.py's `_security_headers` middleware now allows
cdn.jsdelivr.net in script-src/style-src ONLY for /api/docs and
/api/redoc — every other route's CSP is byte-for-byte unchanged, so
this doesn't loosen the site-wide policy.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


async def _get(path: str):
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(path)


class TestApiDocsCspAllowsTheSwaggerUiCdn:
    async def test_api_docs_csp_allows_jsdelivr_script_and_style(self):
        resp = await _get("/api/docs")
        csp = resp.headers.get("content-security-policy", "")
        assert "https://cdn.jsdelivr.net" in csp
        script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
        style_src = csp.split("style-src", 1)[1].split(";", 1)[0]
        assert "https://cdn.jsdelivr.net" in script_src
        assert "https://cdn.jsdelivr.net" in style_src

    async def test_api_redoc_csp_also_allows_jsdelivr(self):
        resp = await _get("/api/redoc")
        csp = resp.headers.get("content-security-policy", "")
        assert "https://cdn.jsdelivr.net" in csp

    async def test_every_other_route_keeps_the_exact_original_csp_unchanged(self):
        resp = await _get("/api/openapi.json")
        csp = resp.headers.get("content-security-policy", "")
        assert "cdn.jsdelivr.net" not in csp
        assert csp == (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://auremcto.com https://bzrcdn.openai.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://auremcto.com wss://auremcto.com "
            "https://openrouter.ai https://api.github.com "
            "https://bzrcdn.openai.com https://bzr.openai.com; "
            "frame-src 'self' blob:; "
            "font-src 'self' data:;"
        )

    async def test_every_other_security_header_on_api_docs_is_unchanged(self):
        resp = await _get("/api/docs")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
