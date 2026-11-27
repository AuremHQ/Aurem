"""
tests/test_kit_sitemap_merge.py — Visibility Kit 2.0 P0-2, B2/B7.

sitemap.xml on the Kit path is ONLY merge_lastmod: existing <loc>
entries are always preserved, fresh scanned URLs are added on top.
A stale/empty scan can never delete live entries (the PR #386 wipe
is the reason this test exists). A fresh sitemap is rendered ONLY
when no sitemap exists at all — and only from a non-empty scan.
"""
from unittest.mock import AsyncMock, patch

import pytest

from services.seo.kit_engine import KitOptions, build_kit_files

PROJECT = {
    "project_id": "p1", "user_id": "u1",
    "github_owner": "o", "github_repo": "r", "branch": "main",
    "name": "Acme",
}
EXISTING_SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    '  <url>\n    <loc>https://acme.test/real-page</loc>\n'
    '    <lastmod>2026-08-01</lastmod>\n  </url>\n'
    '  <url>\n    <loc>https://acme.test/other</loc>\n'
    '    <lastmod>2026-08-01</lastmod>\n  </url>\n'
    "</urlset>\n"
)


def _fetch_factory(existing_sitemap):
    async def _fetch(owner, repo, path, ref, token):
        if path == "sitemap.xml":
            return existing_sitemap
        return None
    return _fetch


@pytest.mark.asyncio
async def test_sitemap_empty_scan_preserves_existing_entries():
    with patch("services.seo.kit_engine.fetch_file",
               new=AsyncMock(side_effect=_fetch_factory(EXISTING_SITEMAP))):
        built = await build_kit_files(
            project=PROJECT, requested_items=["sitemap_auto"],
            token="tok", scan_urls=[], site_meta={}, bot_policy={},
            options=KitOptions(), file_tree=["sitemap.xml"])
    out = built["files"]["sitemap.xml"]
    assert "https://acme.test/real-page" in out
    assert "https://acme.test/other" in out
    assert out.count("<loc>") == 2  # no dupes, nothing wiped


@pytest.mark.asyncio
async def test_sitemap_fresh_urls_merge_on_top_of_existing():
    with patch("services.seo.kit_engine.fetch_file",
               new=AsyncMock(side_effect=_fetch_factory(EXISTING_SITEMAP))):
        built = await build_kit_files(
            project=PROJECT, requested_items=["sitemap_auto"],
            token="tok", scan_urls=[{"loc": "https://acme.test/new"}],
            site_meta={}, bot_policy={}, options=KitOptions(),
            file_tree=["sitemap.xml"])
    out = built["files"]["sitemap.xml"]
    assert "https://acme.test/real-page" in out
    assert "https://acme.test/new" in out
    assert out.count("<loc>") == 3


@pytest.mark.asyncio
async def test_sitemap_regenerated_only_when_none_exists():
    with patch("services.seo.kit_engine.fetch_file",
               new=AsyncMock(side_effect=_fetch_factory(None))):
        built = await build_kit_files(
            project=PROJECT, requested_items=["sitemap_auto"],
            token="tok", scan_urls=[{"loc": "https://acme.test/a"}],
            site_meta={}, bot_policy={}, options=KitOptions(),
            file_tree=[])
    out = built["files"]["sitemap.xml"]
    assert "https://acme.test/a" in out


@pytest.mark.asyncio
async def test_sitemap_no_existing_and_empty_scan_is_conflict():
    with patch("services.seo.kit_engine.fetch_file",
               new=AsyncMock(side_effect=_fetch_factory(None))):
        built = await build_kit_files(
            project=PROJECT, requested_items=["sitemap_auto"],
            token="tok", scan_urls=[], site_meta={}, bot_policy={},
            options=KitOptions(), file_tree=[])
    assert "sitemap.xml" not in built["files"]
    assert "sitemap_auto" in built["conflicts"]
    assert "sitemap_auto" not in built["applied"]
