"""
tests/test_r10b_rollback_verifies_landed_2026_09_04.py

FOCUS MODE (2026-09-04) — R10b, paired with R10 (same round). The
founder asked for a named test `t_rollback_verifies_landed`: rollback
must only report "done" AFTER independently verifying the revert
commit actually landed on the branch HEAD; a verify failure must
report "rollback failed" (with a reason), never "done"/"Rolled back".

INVESTIGATION FINDING: this was ALREADY fully implemented and tested
in the earlier T2/R10 fix (2026-08-30) —
`services/loop_rollback.py::run_rollback` calls
`github_api_writer.verify_branch_head()` after the revert commit and
only sets `rollback_status="done", rollback_verified=True` when that
independently confirms the SHA landed; otherwise it sets
`rollback_status="failed", rollback_verified=False` with a clear
"could not be confirmed" reason AND logs a durable
`ship_rollback_failed` trust event. See
`test_t2_rollback_pr_gap_hardening_2026_08_30.py`'s
`test_t_rollback_verifies_then_done` / `test_t_rollback_blip_reports_
failed` (7/7 passing, re-verified this round). No code change was
needed for R10b — this test just gives the founder's exact requested
name a direct, traceable entry point re-asserting the same contract.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import loop_rollback


@pytest.mark.asyncio
async def test_rollback_verifies_landed_before_reporting_done():
    from services import github_api_writer as gw

    async def _fake_revert(**kw):
        return {"sha": "f00d123", "full_sha": "f00d123deadbeefcafe",
                "html_url": "https://github.com/o/r/commit/f00d123deadbeefcafe"}

    async def _fake_verify_landed(owner, repo, branch, expected_sha, token, **kw):
        return {"verified": True, "attempts": 1, "last_sha": expected_sha}

    db = MagicMock()
    set_calls = []

    async def _upd(filt, ops):
        if "$set" in ops:
            set_calls.append(ops["$set"])
    db.loop_sessions.update_one = AsyncMock(side_effect=_upd)
    db.loop_run_log.insert_one = AsyncMock()
    db.rollback_attempts.insert_one = AsyncMock()
    db.rollback_attempts.update_one = AsyncMock()

    with patch.object(gw, "revert_commit", _fake_revert), \
         patch.object(gw, "verify_branch_head", _fake_verify_landed):
        await loop_rollback.run_rollback(
            db=db, loop_id="loop_r10b_landed",
            project={"github_owner": "o", "github_repo": "r", "branch": "main", "user_id": "u1"},
            commit_sha="deadbeef1234567", user_token="ghp_x",
            author_name="Test", author_email="dev@example.com",
        )

    status_sets = [s for s in set_calls if "rollback_status" in s]
    final = status_sets[-1]
    # The revert LANDED (verified) — only then may status be "done".
    assert final["rollback_status"] == "done"
    assert final["rollback_verified"] is True


@pytest.mark.asyncio
async def test_rollback_never_reports_done_when_revert_does_not_land():
    from services import github_api_writer as gw

    async def _fake_revert(**kw):
        return {"sha": "f00d123", "full_sha": "f00d123deadbeefcafe",
                "html_url": "https://github.com/o/r/commit/f00d123deadbeefcafe"}

    async def _fake_verify_did_not_land(owner, repo, branch, expected_sha, token, **kw):
        # The commit call "succeeded" but branch HEAD never actually
        # moved to it (e.g. a protected-branch rule silently rejected
        # it, or a race with another push) — the exact case this test
        # guards against ever being reported as "Rolled back".
        return {"verified": False, "attempts": 10, "last_sha": "some_other_sha"}

    db = MagicMock()
    set_calls = []

    async def _upd(filt, ops):
        if "$set" in ops:
            set_calls.append(ops["$set"])
    db.loop_sessions.update_one = AsyncMock(side_effect=_upd)
    db.loop_run_log.insert_one = AsyncMock()
    db.rollback_attempts.insert_one = AsyncMock()
    db.rollback_attempts.update_one = AsyncMock()
    db.trust_surface_events.insert_one = AsyncMock()

    with patch.object(gw, "revert_commit", _fake_revert), \
         patch.object(gw, "verify_branch_head", _fake_verify_did_not_land):
        await loop_rollback.run_rollback(
            db=db, loop_id="loop_r10b_not_landed",
            project={"github_owner": "o", "github_repo": "r", "branch": "main", "user_id": "u1"},
            commit_sha="deadbeef1234567", user_token="ghp_x",
            author_name="Test", author_email="dev@example.com",
        )

    status_sets = [s for s in set_calls if "rollback_status" in s]
    final = status_sets[-1]
    assert final["rollback_status"] == "failed"
    assert final["rollback_status"] != "done"
    assert final["rollback_verified"] is False
    assert "could not be confirmed" in final["rollback_error"]
