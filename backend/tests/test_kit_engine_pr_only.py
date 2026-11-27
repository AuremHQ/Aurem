"""
tests/test_kit_engine_pr_only.py — Visibility Kit 2.0 P0-2.

PR-only invariant (B3): run_visibility_kit() NEVER commits to the
base branch. Every non-dry-run ships as: throwaway branch ->
commit_files to THAT branch -> open_or_reuse_pr -> label. dry_run
makes zero GitHub write calls and returns patches + unified diffs.

Style follows tests/test_visibility_kit.py: plain pytest functions,
AsyncMock/MagicMock, patch() on the module-under-test's imports.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import ExitStack, contextmanager

import pytest

from services.seo import kit_engine
from services.seo.kit_engine import KitOptions, run_visibility_kit

PROJECT = {
    "project_id": "p1", "user_id": "u1",
    "github_owner": "o", "github_repo": "r", "branch": "main",
    "name": "Acme", "live_url": "https://acme.test",
}
INDEX_HTML = "<html><head></head><body><p>Hello</p></body></html>"


def _fake_db():
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    return db


async def _fake_fetch(owner, repo, path, ref, token):
    if path in ("index.html", "public/index.html"):
        return INDEX_HTML
    return None


@contextmanager
def _patch_github():
    """Patch every GitHub-touching import on kit_engine. Yields the
    dict of mocks; callers override return values per test.
    (patch.multiple only returns a mocks dict for DEFAULT values, so
    the mocks are built explicitly and patched one by one.)"""
    mocks = {
        "fetch_file": AsyncMock(side_effect=_fake_fetch),
        "find_html_entry_path": AsyncMock(return_value=None),
        "create_or_reuse_branch": AsyncMock(return_value=(True, None)),
        "commit_files": AsyncMock(return_value={"ok": True, "sha": "abc"}),
        "open_or_reuse_pr": AsyncMock(return_value={
            "ok": True, "pr_url": "https://github.com/o/r/pull/7",
            "pr_number": 7, "reused": False}),
        "add_pr_label": AsyncMock(return_value=(True, None)),
        "resolve_git_identity": AsyncMock(return_value=("Bot", "b@x.test")),
    }
    with ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch(f"services.seo.kit_engine.{name}", mock))
        yield mocks


@pytest.mark.asyncio
async def test_pr_only_commit_goes_to_throwaway_branch_never_main():
    db = _fake_db()
    with _patch_github() as mocks:
        result = await run_visibility_kit(
            db, project=PROJECT, requested_items=["ai_crawler_policy"],
            token="tok", file_tree=["index.html"])
    assert result["ok"] is True
    assert result["branch"] == "auremcto/ship-visibility-kit-p1"
    assert result["branch"] != "main"
    # commit_files(owner, repo, branch, token, files, msg, ...) —
    # branch is the 3rd positional arg: the throwaway branch, never main.
    assert mocks["commit_files"].call_args.args[2] == "auremcto/ship-visibility-kit-p1"
    assert mocks["commit_files"].call_args.args[2] != "main"
    pr_kwargs = mocks["open_or_reuse_pr"].call_args.kwargs
    assert pr_kwargs["head_branch"] == "auremcto/ship-visibility-kit-p1"
    assert pr_kwargs["base_branch"] == "main"


@pytest.mark.asyncio
async def test_pr_only_kit_pr_labelled_at_creation():
    db = _fake_db()
    with _patch_github() as mocks:
        result = await run_visibility_kit(
            db, project=PROJECT, requested_items=["ai_crawler_policy"],
            token="tok", file_tree=["index.html"])
    assert result["label_ok"] is True
    _, kwargs = mocks["add_pr_label"].call_args
    assert kwargs["label"] == kit_engine.KIT_PR_LABEL
    assert kwargs["label"].startswith("auremcto/visibility-kit-")
    assert kwargs["pr_number"] == 7


@pytest.mark.asyncio
async def test_pr_only_reused_pr_still_gets_label():
    db = _fake_db()
    with _patch_github() as mocks:
        mocks["open_or_reuse_pr"].return_value = {
            "ok": True, "pr_url": "https://github.com/o/r/pull/7",
            "pr_number": 7, "reused": True}
        result = await run_visibility_kit(
            db, project=PROJECT, requested_items=["ai_crawler_policy"],
            token="tok", file_tree=["index.html"])
    assert result["reused"] is True
    assert mocks["add_pr_label"].call_count == 1  # label even on reuse


@pytest.mark.asyncio
async def test_dry_run_returns_diffs_and_makes_zero_github_writes():
    db = _fake_db()
    with _patch_github() as mocks:
        result = await run_visibility_kit(
            db, project=PROJECT,
            requested_items=["ai_crawler_policy", "llms_txt"],
            token="tok", file_tree=["index.html"],
            options=KitOptions(dry_run=True),
            scan_urls=[{"loc": "https://acme.test/"}])
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["committed"] is False
    assert result["diffs"]["robots.txt"].startswith("---")
    assert "AI crawlers (managed by AUREM)" in result["diffs"]["robots.txt"]
    mocks["create_or_reuse_branch"].assert_not_called()
    mocks["commit_files"].assert_not_called()
    mocks["open_or_reuse_pr"].assert_not_called()
    mocks["add_pr_label"].assert_not_called()
    db.visibility_state.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_pr_only_state_written_as_pr_created():
    db = _fake_db()
    with _patch_github():
        await run_visibility_kit(
            db, project=PROJECT, requested_items=["ai_crawler_policy"],
            token="tok", file_tree=["index.html"])
    args, _kwargs = db.visibility_state.update_one.call_args
    assert args[1]["$set"]["status"] == "pr_created"
    assert db.visibility_applications.insert_one.call_count == 1
    app = db.visibility_applications.insert_one.call_args.args[0]
    assert app["status"] == "open"
    assert app["label"] == kit_engine.KIT_PR_LABEL
