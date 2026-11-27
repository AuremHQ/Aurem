"""
tests/test_kit_csr_warning.py — Visibility Kit 2.0 P0-2, B8/B9.

Framework-aware handling: React/Vite CSR gets the warning (Google
renders the static index.html, a non-JS fetcher may not); Next.js
App Router, WordPress and Shopify get NO HTML inject and a
copy-paste snippet in the PR body instead. Unknown frameworks get a
static inject + PR note.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.seo.kit_engine import CSR_WARNING, run_visibility_kit

BASE_PROJECT = {
    "project_id": "p1", "user_id": "u1",
    "github_owner": "o", "github_repo": "r", "branch": "main",
    "name": "Acme", "live_url": "https://acme.test",
}
INDEX_HTML = "<html><head></head><body><div id=\"root\"></div></body></html>"


def _fake_db():
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    return db


async def _fake_fetch(owner, repo, path, ref, token):
    if path in ("index.html", "public/index.html"):
        return INDEX_HTML
    return None


async def _run_with_tree(tree, package_json, items):
    """Run the kit with all GitHub writes mocked; return
    (result, open_pr_body)."""
    captured = {}

    async def _fake_open_pr(**kwargs):
        captured["body"] = kwargs.get("body", "")
        return {"ok": True, "pr_url": "https://github.com/o/r/pull/7",
                "pr_number": 7, "reused": False}

    with patch.multiple(
        "services.seo.kit_engine",
        fetch_file=AsyncMock(side_effect=_fake_fetch),
        find_html_entry_path=AsyncMock(return_value=None),
        create_or_reuse_branch=AsyncMock(return_value=(True, None)),
        commit_files=AsyncMock(return_value={"ok": True}),
        open_or_reuse_pr=AsyncMock(side_effect=_fake_open_pr),
        add_pr_label=AsyncMock(return_value=(True, None)),
        resolve_git_identity=AsyncMock(return_value=("Bot", "b@x.test")),
    ):
        result = await run_visibility_kit(
            _fake_db(), project=BASE_PROJECT, requested_items=items,
            token="tok", file_tree=tree,
            site_meta={"package_json": package_json},
            scan_urls=[{"loc": "https://acme.test/"}])
    return result, captured


@pytest.mark.asyncio
async def test_csr_react_gets_warning_but_still_injects_static_html():
    result, captured = await _run_with_tree(
        ["index.html", "package.json"],
        {"dependencies": {"react": "18.0.0"}},
        ["structured_data"])
    assert result["detected_framework"] == "react"
    assert result["csr_warning"] == CSR_WARNING
    assert "Google renders it" in result["framework_notes"][0]
    assert "Google renders it" in captured["body"]
    assert "structured_data" in result["items_covered"]


@pytest.mark.asyncio
async def test_csr_next_app_router_skips_html_inject_with_snippet():
    result, captured = await _run_with_tree(
        ["app/layout.tsx", "package.json"],
        {"dependencies": {"next": "14.0.0"}},
        ["structured_data", "ai_crawler_policy"])
    assert result["detected_framework"] == "next"
    assert "structured_data" not in result["items_covered"]
    assert "ai_crawler_policy" in result["items_covered"]
    assert "app/layout.tsx" in captured["body"]
    assert "metadata" in captured["body"]


@pytest.mark.asyncio
async def test_csr_wordpress_skips_html_inject_with_mu_plugin_snippet():
    result, captured = await _run_with_tree(
        ["wp-content/themes/x/style.css", "wp-config.php"],
        None,
        ["structured_data", "preferred_sources", "ai_crawler_policy"])
    assert result["detected_framework"] == "wordpress"
    assert "structured_data" not in result["items_covered"]
    assert "preferred_sources" not in result["items_covered"]
    assert result["items_covered"] == ["ai_crawler_policy"]
    assert "mu-plugin" in captured["body"] or "mu-plugins" in captured["body"]


@pytest.mark.asyncio
async def test_csr_shopify_skips_html_inject_with_liquid_snippet():
    result, captured = await _run_with_tree(
        ["layout/theme.liquid", "config/settings_schema.json"],
        None,
        ["structured_data", "ai_crawler_policy"])
    assert result["detected_framework"] == "shopify"
    assert "structured_data" not in result["items_covered"]
    assert "ai_crawler_policy" in result["items_covered"]
    assert "theme.liquid" in captured["body"]


@pytest.mark.asyncio
async def test_csr_unknown_framework_static_inject_with_pr_note():
    result, captured = await _run_with_tree(
        ["index.html"], {"dependencies": {"vue": "3.0.0"}},
        ["structured_data"])
    assert result["unknown_framework_fallback"] is True
    assert "structured_data" in result["items_covered"]
    assert "unknown" in captured["body"].lower()
