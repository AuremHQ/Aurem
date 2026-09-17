"""
tests/test_preview_reachability_2026_09_17.py

Founder-reported: the Preview panel's "Live now" iframe can show a
permanently blank/white result with zero explanation when the
underlying deployment doesn't exist or is down — the panel only ever
inferred state from the iframe's own `onLoad` event, which fires even
for a browser-level connection-error page (a false "loaded" signal).

`GET /projects/{project_id}/preview/reachability` does a real
server-side probe (no CORS/iframe blind spots) so the frontend can
show an honest message. Covers all outcome branches:
  - no preview_url/live_url configured at all       -> no_deployment
  - connection-level failure (nothing listening)     -> no_deployment
  - a response came back but it's a 5xx              -> deployment_down
  - a read-timeout (connected, but no reply in time) -> deployment_down
  - a normal 2xx/3xx/4xx response                    -> reachable=True
"""
import importlib
from unittest.mock import patch

import httpx
import pytest
import respx

cto_projects = importlib.import_module("routers.cto_projects")
preview_mod = importlib.import_module("routers.cto_projects.preview")


def _patch_auth_and_db(proj_doc):
    async def fake_current_dev(_authz):
        return {"user_id": "u-1"}

    class _Coll:
        async def find_one(self, _q, _proj):
            return proj_doc

    class _DB:
        cto_projects = _Coll()

    return (
        patch.object(cto_projects, "current_dev", fake_current_dev),
        patch.object(cto_projects, "require_db", lambda: _DB()),
    )


@pytest.mark.asyncio
async def test_no_url_configured_is_no_deployment():
    p1, p2 = _patch_auth_and_db({"preview_url": None, "live_url": None})
    with p1, p2:
        res = await preview_mod.check_preview_reachability("p_x", authorization="Bearer x")
    assert res["ok"] is True
    assert res["reachable"] is False
    assert res["reason"] == "no_deployment"
    assert res["checked_url"] is None


@pytest.mark.asyncio
@respx.mock
async def test_healthy_response_is_reachable():
    respx.get("https://ok.example.com").respond(200, text="hi")
    p1, p2 = _patch_auth_and_db({"preview_url": "https://ok.example.com", "live_url": None})
    with p1, p2:
        res = await preview_mod.check_preview_reachability("p_x", authorization="Bearer x")
    assert res["reachable"] is True
    assert res["status_code"] == 200
    assert res["checked_url"] == "https://ok.example.com"


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_is_no_deployment():
    respx.get("https://dead.example.com").mock(
        side_effect=httpx.ConnectError("nope"),
    )
    p1, p2 = _patch_auth_and_db({"preview_url": "https://dead.example.com", "live_url": None})
    with p1, p2:
        res = await preview_mod.check_preview_reachability("p_x", authorization="Bearer x")
    assert res["reachable"] is False
    assert res["reason"] == "no_deployment"
    assert "dead.example.com" in res["detail"]


@pytest.mark.asyncio
@respx.mock
async def test_5xx_response_is_deployment_down():
    respx.get("https://broken.example.com").respond(502, text="bad gateway")
    p1, p2 = _patch_auth_and_db({"preview_url": "https://broken.example.com", "live_url": None})
    with p1, p2:
        res = await preview_mod.check_preview_reachability("p_x", authorization="Bearer x")
    assert res["reachable"] is False
    assert res["reason"] == "deployment_down"
    assert res["status_code"] == 502


@pytest.mark.asyncio
@respx.mock
async def test_read_timeout_is_deployment_down():
    respx.get("https://slow.example.com").mock(
        side_effect=httpx.ReadTimeout("too slow"),
    )
    p1, p2 = _patch_auth_and_db({"preview_url": "https://slow.example.com", "live_url": None})
    with p1, p2:
        res = await preview_mod.check_preview_reachability("p_x", authorization="Bearer x")
    assert res["reachable"] is False
    assert res["reason"] == "deployment_down"


@pytest.mark.asyncio
@respx.mock
async def test_4xx_response_still_counts_as_reachable():
    # A 404 means a real web server exists and answered - the iframe
    # will render that (informative on its own); it is NOT the same
    # as "nothing is listening at all".
    respx.get("https://notfound.example.com").respond(404, text="nope")
    p1, p2 = _patch_auth_and_db({"preview_url": "https://notfound.example.com", "live_url": None})
    with p1, p2:
        res = await preview_mod.check_preview_reachability("p_x", authorization="Bearer x")
    assert res["reachable"] is True
    assert res["status_code"] == 404
