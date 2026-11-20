# 2026-11-19 - /api/ask alias + backend-served /.well-known/api-catalog.
# The 17:01 EST deploy went red on both: /api/ask never existed as a route
# (ask_router was mounted only at /ask, which nginx never proxies), and the
# api-catalog was served by nginx from the static frontend file as
# application/octet-stream. The backend now serves both.
import pytest
from httpx import ASGITransport, AsyncClient

from server import app


@pytest.mark.asyncio
async def test_api_ask_pricing_returns_json_results():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/ask", params={"query": "pricing"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"], "expected results for pricing"
        assert body.get("error") is None


@pytest.mark.asyncio
async def test_api_ask_post_returns_json_results():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/ask", json={"query": "pricing"})
        assert resp.status_code == 200
        assert resp.json()["results"]


@pytest.mark.asyncio
async def test_ask_root_still_works():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ask", params={"query": "pricing"})
        assert resp.status_code == 200
        assert resp.json()["results"]


@pytest.mark.asyncio
async def test_well_known_api_catalog_is_linkset_json():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/.well-known/api-catalog")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/linkset+json")
        body = resp.json()
        assert body["linkset"][0]["anchor"] == "https://auremcto.com/"
        descs = body["linkset"][0]["service-desc"]
        assert any(d["href"] == "https://auremcto.com/api/openapi.json" for d in descs)


@pytest.mark.asyncio
async def test_well_known_oauth_authorization_server_is_json():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json().get("issuer") == "https://auremcto.com/api/aurem-dev"
