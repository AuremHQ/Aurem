"""
tests/test_kit_robots_managed.py — Visibility Kit 2.0 P0-2, B2.

robots.txt on the Kit path is ONLY apply_managed_block: every
existing line is preserved byte-for-byte, only the managed block is
appended/replaced. The orchestrator's patch_robots_txt (whole-file
regenerate) is never imported or called on this path — the dual
writer is dead on the Kit path, not just discouraged.
"""
from unittest.mock import AsyncMock, patch

import pytest

from services.seo import kit_engine
from services.seo.kit_engine import build_kit_files, KitOptions

PROJECT = {
    "project_id": "p1", "user_id": "u1",
    "github_owner": "o", "github_repo": "r", "branch": "main",
    "name": "Acme",
}
EXISTING_ROBOTS = (
    "User-agent: *\nDisallow: /admin\n\n"
    "Sitemap: https://acme.test/sitemap.xml\n"
)


async def _fetch_with_robots(owner, repo, path, ref, token):
    if path == "robots.txt":
        return EXISTING_ROBOTS
    return None


@pytest.mark.asyncio
async def test_robots_managed_block_preserves_existing_lines():
    with patch("services.seo.kit_engine.fetch_file",
               new=AsyncMock(side_effect=_fetch_with_robots)):
        built = await build_kit_files(
            project=PROJECT, requested_items=["ai_crawler_policy"],
            token="tok", scan_urls=[], site_meta={}, bot_policy={},
            options=KitOptions(), file_tree=["robots.txt"])
    out = built["files"]["robots.txt"]
    assert "Disallow: /admin" in out
    assert "Sitemap: https://acme.test/sitemap.xml" in out
    assert "User-agent: GPTBot\nDisallow: /" in out  # training default deny
    assert "User-agent: OAI-SearchBot\nAllow: /" in out  # retrieval allowed


@pytest.mark.asyncio
async def test_robots_managed_block_idempotent_no_duplicate():
    with patch("services.seo.kit_engine.fetch_file",
               new=AsyncMock(side_effect=_fetch_with_robots)):
        built = await build_kit_files(
            project=PROJECT, requested_items=["ai_crawler_policy"],
            token="tok", scan_urls=[], site_meta={}, bot_policy={},
            options=KitOptions(), file_tree=["robots.txt"])
    once = built["files"]["robots.txt"]
    from services.seo import robots_txt as robots_gen
    twice = robots_gen.apply_managed_block(once, {"GPTBot": "allow"})
    assert twice.count(robots_gen._START) == 1
    assert "Disallow: /admin" in twice
    assert "User-agent: GPTBot\nAllow: /" in twice


@pytest.mark.asyncio
async def test_robots_no_existing_file_gets_managed_block_only():
    async def _fetch_none(owner, repo, path, ref, token):
        return None

    with patch("services.seo.kit_engine.fetch_file",
               new=AsyncMock(side_effect=_fetch_none)):
        built = await build_kit_files(
            project=PROJECT, requested_items=["ai_crawler_policy"],
            token="tok", scan_urls=[], site_meta={}, bot_policy={},
            options=KitOptions(), file_tree=[])
    out = built["files"]["robots.txt"]
    from services.seo import robots_txt as robots_gen
    assert out.startswith(robots_gen._START)


def test_kit_engine_has_no_whole_file_robots_writer():
    # The dual writer is killed by not existing on this path at all.
    assert not hasattr(kit_engine, "patch_robots_txt")
    assert not hasattr(kit_engine, "patch_sitemap")
    assert not hasattr(kit_engine, "patch_schema_markup")
