"""
tests/test_kit_merge_webhook.py — Visibility Kit 2.0 P0-4.

The webhook's kit branch + the 6h fallback cron share ONE promotion
helper (promote_kit_pr_merged): pr_created -> pr_merged on a merged
PR event, matched on (pr_number, branch). A lookup failure
(get_pr_status ok=False) is "could not verify", never "unmerged" —
the row is left alone for the next pass.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.seo.kit_engine import promote_kit_pr_merged, reconcile_kit_prs

APP = {
    "_id": "app1", "project_id": "p1",
    "branch": "auremcto/ship-visibility-kit-p1",
    "pr_number": 7, "items": ["ai_crawler_policy", "llms_txt"],
    "status": "open",
}


def _fake_db(app_doc=None):
    db = MagicMock()
    db.visibility_applications.find_one = AsyncMock(return_value=app_doc)
    db.visibility_applications.update_one = AsyncMock()
    db.visibility_applications.find = MagicMock(return_value=MagicMock(
        to_list=AsyncMock(return_value=[app_doc] if app_doc else [])))
    db.visibility_state.update_many = AsyncMock(
        return_value=MagicMock(modified_count=2))
    db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1",
        "github_owner": "o", "github_repo": "r", "branch": "main"})
    return db


@pytest.mark.asyncio
async def test_promote_merged_moves_pr_created_to_pr_merged():
    db = _fake_db(APP)
    result = await promote_kit_pr_merged(
        db, pr_number=7, branch="auremcto/ship-visibility-kit-p1",
        merge_commit_sha="abc1234")
    assert result["promoted"] == 2
    assert result["project_id"] == "p1"
    args, _kwargs = db.visibility_state.update_many.call_args
    assert args[1]["$set"]["status"] == "pr_merged"
    # Only pr_created rows move — already-live rows are untouched.
    assert db.visibility_state.update_many.call_args.args[0]["status"] == "pr_created"
    app_args, _app_kwargs = db.visibility_applications.update_one.call_args
    assert app_args[1]["$set"]["status"] == "merged"
    assert app_args[1]["$set"]["merge_commit_sha"] == "abc1234"


@pytest.mark.asyncio
async def test_promote_merged_no_application_promotes_nothing():
    db = _fake_db(None)
    result = await promote_kit_pr_merged(
        db, pr_number=99, branch="auremcto/ship-visibility-kit-p9")
    assert result == {"promoted": 0, "project_id": None, "reason": "no_application"}
    db.visibility_state.update_many.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_cron_promotes_on_merged_pr_status():
    db = _fake_db(APP)
    with patch("services.pat_vault.get_repo_token_or_error",
               new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.seo.kit_engine.get_pr_status",
               new=AsyncMock(return_value={
                   "ok": True, "merged": True, "state": "closed",
                   "merge_commit_sha": "abc1234"})), \
         patch("services.seo.kit_engine.verify_kit_live",
               new=AsyncMock(return_value={"live": False})):
        result = await reconcile_kit_prs(db)
    assert result["checked"] == 1
    assert result["promoted_merged"] == 2
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_reconcile_cron_lookup_failure_leaves_row_alone():
    db = _fake_db(APP)
    with patch("services.pat_vault.get_repo_token_or_error",
               new=AsyncMock(return_value=("tok", None, None))), \
         patch("services.seo.kit_engine.get_pr_status",
               new=AsyncMock(return_value={
                   "ok": False, "merged": False, "state": "unknown",
                   "merge_commit_sha": None})):
        result = await reconcile_kit_prs(db)
    # Could not verify != unmerged: no promotion, no close, no error.
    assert result["promoted_merged"] == 0
    db.visibility_state.update_many.assert_not_called()
    db.visibility_applications.update_one.assert_not_called()
