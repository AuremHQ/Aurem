# NLWeb /ask endpoint tests (Aurem fix work, item 5).
import pytest
from httpx import ASGITransport, AsyncClient
from server import app
@pytest.mark.asyncio
async def test_get_ask_pricing_returns_results():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ask", params={"query": "pricing"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"], "expected results for pricing"
    assert body.get("error") is None
@pytest.mark.asyncio
async def test_get_ask_generic_query_returns_results():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ask", params={"query": "what is Ora"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"], "expected results for a generic query"
    assert body.get("error") is None
@pytest.mark.asyncio
async def test_get_ask_nonsense_returns_no_results():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ask", params={"query": "zzqxyy wibble"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["error"]["code"] == "NO_RESULTS"
@pytest.mark.asyncio
async def test_post_ask_body_works():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/ask", json={"query": "pricing"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"], "expected results for POST /ask"
    assert body.get("error") is None
