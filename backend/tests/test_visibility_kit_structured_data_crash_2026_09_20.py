"""
tests/test_visibility_kit_structured_data_crash_2026_09_20.py

Founder-reported live bug (via TJSNDHU/Aurem, used only as the
connected-project test subject — the bug is in Aurem's own apply
code, not that repo's content): the "JSON-LD + meta/OG" item
(`structured_data`) kept "still showing [as an] issue", and "bulk
apply also failed" too.

Root cause: `apply_visibility_kit()`'s `structured_data` branch read
`site_meta["schema"]` with UNSAFE bracket access. Nothing anywhere in
this codebase ever writes `site_meta["schema"]` (confirmed via
`grep -rn '"schema":' services/ routers/` — zero hits) — so it was
ALWAYS `{}` for every real project, and this line always raised
`KeyError('schema')`, crashing the request (reproduced live: the
`/apply` endpoint returned HTTP 499 with the backend log showing the
raw traceback). Because `apply_visibility_kit()` processes ALL
requested items in ONE call before anything is committed, this single
unhandled exception aborted bulk apply entirely too — even the other
4 items (which have no such bug) never got a chance to commit.

Fix: resolve a real `schema` dict from data already on the project
(`project["name"]`/`repo`, `project["preview_url"]`/`live_url`)
instead of trusting a field nothing ever populates, mirroring the
`.get()` + project-field-fallback pattern `preferred_sources`/
`llms_txt` already used. Only when there is truly no URL anywhere
(no schema.url, no site_meta.domain, no preview_url, no live_url)
does this become a graceful conflict (skip, like the pre-existing
"index.html not found" conflicts) instead of a crash.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.visibility.apply import apply_visibility_kit


def _fake_fetch_factory(index_html="<html><head><title>x</title></head><body></body></html>"):
    async def fake_fetch(owner, repo, path, branch, token):
        if path == "index.html":
            return index_html
        return None
    return fake_fetch


@pytest.mark.asyncio
async def test_structured_data_no_longer_crashes_when_site_meta_schema_is_absent():
    """The exact reported bug: site_meta has NO 'schema' key at all
    (the real-world state for every project, since nothing ever
    writes it). Must not raise KeyError — must resolve a usable
    schema from the project's own preview_url/name and succeed."""
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    project = {
        "project_id": "p1", "user_id": "u1", "name": "aurem-demo/frontend",
        "github_owner": "o", "github_repo": "r", "branch": "main",
        "preview_url": "https://aurem-demo.example.com",
    }
    with patch("services.visibility.apply.fetch_file", new=AsyncMock(side_effect=_fake_fetch_factory())), \
         patch("services.visibility.apply.resolve_git_identity", new=AsyncMock(return_value=("Bot", "b@x.com"))), \
         patch("services.visibility.apply.create_or_reuse_branch", new=AsyncMock(return_value=(True, None))), \
         patch("services.visibility.apply.commit_files", new=AsyncMock(return_value={"ok": True})), \
         patch("services.visibility.apply.open_or_reuse_pr", new=AsyncMock(return_value={
             "ok": True, "pr_url": "https://github.com/o/r/pull/1",
             "pr_number": 1, "reused": False, "error": None})):
        # site_meta={} — no "schema" key, no "domain" key. This is the
        # literal shape every real project has today.
        result = await apply_visibility_kit(
            db, project=project, requested_items=["structured_data"],
            token="t", scan_urls=[], site_meta={}, bot_policy={},
        )
    assert result["ok"] is True
    assert "structured_data" not in (result.get("conflicts") or [])


@pytest.mark.asyncio
async def test_structured_data_uses_project_preview_url_and_name_as_schema_fallback():
    """Behavioural proof the derived schema is actually USED (not just
    non-crashing) — the generated JSON-LD carries the real project
    domain/name, not empty placeholders."""
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    project = {
        "project_id": "p1", "user_id": "u1", "name": "Acme Corp Site",
        "github_owner": "o", "github_repo": "r", "branch": "main",
        "preview_url": "https://acme.example.com",
    }
    captured = {}

    async def capture_commit(*args, **kwargs):
        captured["files"] = kwargs.get("files") if "files" in kwargs else args[4]
        return {"ok": True}

    with patch("services.visibility.apply.fetch_file", new=AsyncMock(side_effect=_fake_fetch_factory())), \
         patch("services.visibility.apply.resolve_git_identity", new=AsyncMock(return_value=("Bot", "b@x.com"))), \
         patch("services.visibility.apply.create_or_reuse_branch", new=AsyncMock(return_value=(True, None))), \
         patch("services.visibility.apply.commit_files", new=AsyncMock(side_effect=capture_commit)), \
         patch("services.visibility.apply.open_or_reuse_pr", new=AsyncMock(return_value={
             "ok": True, "pr_url": "https://github.com/o/r/pull/1",
             "pr_number": 1, "reused": False, "error": None})):
        result = await apply_visibility_kit(
            db, project=project, requested_items=["structured_data"],
            token="t", scan_urls=[], site_meta={}, bot_policy={},
        )
    assert result["ok"] is True
    html = captured["files"]["index.html"]
    assert "acme.example.com" in html
    assert "Acme Corp Site" in html


@pytest.mark.asyncio
async def test_structured_data_gracefully_conflicts_when_truly_no_url_anywhere():
    """When there is genuinely no schema.url, no site_meta.domain, and
    no preview_url/live_url on the project, this must be a graceful
    per-item conflict (like the existing 'index.html not found'
    conflicts) — never a crash."""
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    project = {"project_id": "p1", "user_id": "u1",
               "github_owner": "o", "github_repo": "r", "branch": "main"}
    with patch("services.visibility.apply.fetch_file", new=AsyncMock(side_effect=_fake_fetch_factory())), \
         patch("services.visibility.apply.resolve_git_identity", new=AsyncMock(return_value=("Bot", "b@x.com"))), \
         patch("services.visibility.apply.create_or_reuse_branch", new=AsyncMock(return_value=(True, None))):
        result = await apply_visibility_kit(
            db, project=project, requested_items=["structured_data"],
            token="t", scan_urls=[], site_meta={}, bot_policy={},
        )
    assert result["ok"] is False
    assert any("no site URL/domain on file" in c for c in result["conflicts"])


@pytest.mark.asyncio
async def test_bulk_apply_all_five_items_no_longer_aborted_by_structured_data_crash():
    """The exact 'bulk apply also failed' report: requesting all 5
    auto items together used to abort the ENTIRE call (nothing
    committed, not even the other 4 unrelated items) the instant
    structured_data's KeyError fired. Must now succeed for all 5."""
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    project = {
        "project_id": "p1", "user_id": "u1", "name": "aurem-demo/frontend",
        "github_owner": "o", "github_repo": "r", "branch": "main",
        "preview_url": "https://aurem-demo.example.com",
    }
    with patch("services.visibility.apply.fetch_file", new=AsyncMock(side_effect=_fake_fetch_factory())), \
         patch("services.visibility.apply.resolve_git_identity", new=AsyncMock(return_value=("Bot", "b@x.com"))), \
         patch("services.visibility.apply.create_or_reuse_branch", new=AsyncMock(return_value=(True, None))), \
         patch("services.visibility.apply.commit_files", new=AsyncMock(return_value={"ok": True})), \
         patch("services.visibility.apply.open_or_reuse_pr", new=AsyncMock(return_value={
             "ok": True, "pr_url": "https://github.com/o/r/pull/1",
             "pr_number": 1, "reused": False, "error": None})):
        result = await apply_visibility_kit(
            db, project=project,
            requested_items=["ai_crawler_policy", "structured_data", "sitemap_auto",
                              "preferred_sources", "llms_txt"],
            token="t", scan_urls=[{"loc": "https://aurem-demo.example.com/"}],
            site_meta={}, bot_policy={},
        )
    assert result["ok"] is True
    assert result.get("conflicts") in (None, [])
