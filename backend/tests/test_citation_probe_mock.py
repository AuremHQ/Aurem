"""
tests/test_citation_probe_mock.py — Visibility Kit 2.0 P0-5.

The `citation_probe` catalog item is a measurement, not a fix: the
probe checks whether the live site serves Kit content a citation
engine can read. There is no API that reports "an AI cited this
site", so the probe MUST NOT claim that — the transport is mocked
(http_get stub) and the test asserts the honest scope note.
"""
import pytest

from services.seo.kit_engine import probe_citation_live
from services.seo import schema_markup as schema_gen

PROJECT = {"project_id": "p1", "live_url": "https://acme.test"}


async def _http_get_marker(url):
    return 200, f"<html><head>{schema_gen._START}</head></html>"


async def _http_get_empty(url):
    return 200, "<html><head></head><body>plain site</body></html>"


async def _http_get_down(url):
    return None, ""


@pytest.mark.asyncio
async def test_citation_probe_live_marker_found():
    result = await probe_citation_live(PROJECT, http_get=_http_get_marker)
    assert result["ok"] is True
    assert result["live"] is True
    assert result["marker_found"] == schema_gen._START
    assert result["live_url"] == "https://acme.test"


@pytest.mark.asyncio
async def test_citation_probe_no_marker_is_not_live():
    result = await probe_citation_live(PROJECT, http_get=_http_get_empty)
    assert result["ok"] is False
    assert result["live"] is False
    assert result["marker_found"] is None


@pytest.mark.asyncio
async def test_citation_probe_site_down_is_not_live():
    result = await probe_citation_live(PROJECT, http_get=_http_get_down)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_citation_probe_never_claims_an_ai_cited_you():
    result = await probe_citation_live(PROJECT, http_get=_http_get_marker)
    assert "does not query any AI" in result["note"]
    assert "cited" not in result["note"].lower().replace("citation", "")
    assert "AI cited" not in str(result)
