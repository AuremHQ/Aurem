"""
2026-09-18 — Bug 1, ROUND 2: 371 real duplicate PRs (Visibility Kit
x5, fix(fix):large_function x many) piled up on a live connected
repo AFTER the 2026-09-17 round-1 fix had already shipped.

Root cause: round 1 only fixed ONE of (at least) four independent
PR-opening call sites — `services/finding_fix_applier.py`. The other
three (`services/loop_engine.py`'s ship-via-PR path,
`services/visibility/apply.py` — Visibility Kit, and
`services/visual_bugfix_pr.py`) each called `open_draft_pr()`
directly with either (a) a still-timestamped `ship_branch_name()`
(so `create_or_reuse_branch`'s "already exists -> ok" never got a
chance to fire) and/or (b) zero check for an already-open PR before
calling create-PR again.

This round centralizes the fix in TWO places so it can't be
half-applied again:
  1. `ship_branch_name()` itself is now truly deterministic (no
     internal timestamp) — same slug in, same branch out, always.
  2. `open_or_reuse_pr()` is the ONE function every PR-opening call
     site must go through — it looks for an existing open PR on the
     head branch first and reuses it, only calling `open_draft_pr`
     when none exists.

Covers:
  - `ship_branch_name` determinism (was the literal root cause).
  - `open_or_reuse_pr` — 2nd call with the same head_branch reuses,
    never calls create-PR twice.
  - `services/visibility/apply.py` — running the SAME Kit apply
    twice for the SAME project lands on the SAME branch and reuses
    the SAME PR (the exact "Visibility Kit x5" repro).
  - `services/visual_bugfix_pr.py` — a retry no longer 422s.
  - `services/loop_engine.py` — source-level guard (same convention
    already used by this file's neighbours,
    test_iter212m115_loop_safety_five_fixes.py, for this
    hard-to-instantiate class) proving the ship-via-PR path now
    keys its branch on `project_id` (stable across runs) instead of
    `self.loop_id` (a fresh UUID every run), and goes through
    `open_or_reuse_pr` instead of calling `open_draft_pr` directly.
"""
from __future__ import annotations

import pytest
import respx
import httpx
from unittest.mock import AsyncMock

from services import loop_safety as ls

GH = "https://api.github.com"


# ─── 1. ship_branch_name determinism ────────────────────────────────
def test_ship_branch_name_is_now_deterministic():
    """The literal root cause: same slug in, must be same branch out,
    every time — no internal timestamp."""
    a = ls.ship_branch_name("visibility-kit-p1")
    b = ls.ship_branch_name("visibility-kit-p1")
    assert a == b, "ship_branch_name must be pure/deterministic — no clock in it"
    assert a.startswith("auremcto/ship-")
    # Different slugs still map to different (but each stable) names.
    assert ls.ship_branch_name("visibility-kit-p2") != a


# ─── 2. open_or_reuse_pr — the centralized dedup primitive ─────────
@pytest.mark.asyncio
@respx.mock
async def test_open_or_reuse_pr_first_call_opens_second_call_reuses():
    respx.get(f"{GH}/repos/o/r/pulls").mock(side_effect=[
        httpx.Response(200, json=[]),  # 1st call: nothing open yet
        httpx.Response(200, json=[{"number": 7, "html_url": "https://github.com/o/r/pull/7"}]),
    ])
    create_pr_mock = respx.post(f"{GH}/repos/o/r/pulls").mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/7"}),
    )
    comment_mock = respx.post(f"{GH}/repos/o/r/issues/7/comments").mock(
        return_value=httpx.Response(201, json={}),
    )

    first = await ls.open_or_reuse_pr(
        owner="o", repo="r", head_branch="auremcto/ship-p1",
        base_branch="main", title="t", body="b", token="tkn",
    )
    assert first["ok"] is True and first["reused"] is False
    assert first["pr_url"] == "https://github.com/o/r/pull/7"
    assert create_pr_mock.call_count == 1

    second = await ls.open_or_reuse_pr(
        owner="o", repo="r", head_branch="auremcto/ship-p1",
        base_branch="main", title="t2", body="b2", token="tkn",
        update_comment="new commit landed",
    )
    assert second["ok"] is True and second["reused"] is True
    assert second["pr_url"] == "https://github.com/o/r/pull/7"
    assert second["pr_number"] == 7
    # THE assertion: create-PR must NOT fire a 2nd time.
    assert create_pr_mock.call_count == 1, (
        "a 2nd run against the same head_branch called create-PR "
        "again — this is exactly how the 371 duplicates happened"
    )
    assert comment_mock.call_count == 1


@pytest.mark.asyncio
async def test_open_or_reuse_pr_soft_fails_without_raising(monkeypatch):
    monkeypatch.setattr(ls, "find_open_pr_for_branch", AsyncMock(return_value=None))
    monkeypatch.setattr(ls, "open_draft_pr", AsyncMock(return_value=(None, "pr_status_500")))
    result = await ls.open_or_reuse_pr(
        owner="o", repo="r", head_branch="b", base_branch="main",
        title="t", body="b", token="tkn",
    )
    assert result["ok"] is False
    assert result["error"] == "pr_status_500"
    assert result["pr_url"] is None


# ─── 3. Visibility Kit — the exact reported repro ("x5") ────────────
def _kit_project():
    return {
        "project_id": "p_kit1", "user_id": "u1",
        "github_owner": "o", "github_repo": "r", "branch": "main",
    }


@pytest.mark.asyncio
async def test_visibility_kit_rerun_same_project_reuses_pr_not_duplicate(monkeypatch):
    """Reported repro: 'Visibility Kit x5' — apply the Kit twice for
    the SAME project (e.g. a second scan later that day, or the next
    day) and confirm it lands on the SAME branch and reuses the SAME
    PR instead of opening a second one."""
    from services.visibility import apply as kit_apply

    monkeypatch.setattr(kit_apply, "fetch_file", AsyncMock(return_value=None))
    monkeypatch.setattr(kit_apply, "commit_files", AsyncMock(return_value={"sha": "abc"}))
    monkeypatch.setattr(kit_apply, "resolve_git_identity",
                         AsyncMock(return_value=("ORA", "ora@aurem.dev")))
    monkeypatch.setattr(kit_apply, "create_or_reuse_branch", AsyncMock(return_value=(True, None)))

    call_log = []

    async def fake_open_or_reuse(**kwargs):
        call_log.append(kwargs["head_branch"])
        if len(call_log) == 1:
            return {"ok": True, "pr_url": "https://github.com/o/r/pull/55",
                     "pr_number": 55, "reused": False, "error": None}
        return {"ok": True, "pr_url": "https://github.com/o/r/pull/55",
                "pr_number": 55, "reused": True, "error": None}

    monkeypatch.setattr(kit_apply, "open_or_reuse_pr", fake_open_or_reuse)

    class _DB:
        class _State:
            async def update_one(self, *a, **k):
                pass
        class _Apps:
            async def insert_one(self, *a, **k):
                pass
        visibility_state = _State()
        visibility_applications = _Apps()

    project = _kit_project()
    common_kwargs = dict(
        db=_DB(), project=project, requested_items=["ai_crawler_policy"],
        token="tkn", scan_urls=[], site_meta={"file_tree": [], "package_json": None},
        bot_policy={},
    )

    res1 = await kit_apply.apply_visibility_kit(**common_kwargs)
    res2 = await kit_apply.apply_visibility_kit(**common_kwargs)

    assert res1["ok"] is True and res2["ok"] is True
    assert len(call_log) == 2
    assert call_log[0] == call_log[1], (
        "same project, re-applied -> must target the SAME head branch "
        "both times (this is what round-1's date-stamped + "
        "internally-timestamped ship_branch_name broke)"
    )
    assert res1["pr_url"] == res2["pr_url"] == "https://github.com/o/r/pull/55"
    assert res1["branch"] == res2["branch"]


# ─── 4. visual_bugfix_pr — retry no longer 422s ─────────────────────
@pytest.mark.asyncio
async def test_visual_bugfix_retry_reuses_pr_instead_of_422(monkeypatch):
    from services import visual_bugfix_pr as vbp

    monkeypatch.setattr(ls, "create_or_reuse_branch", AsyncMock(return_value=(True, None)))
    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", AsyncMock(return_value={"sha": "abc"}))
    import services.git_identity as gi
    monkeypatch.setattr(gi, "resolve_git_identity", AsyncMock(return_value=("ORA", "ora@aurem.dev")))
    monkeypatch.setattr(ls, "find_open_pr_for_branch", AsyncMock(return_value={
        "number": 9, "html_url": "https://github.com/o/r/pull/9",
    }))
    open_pr_mock = AsyncMock()
    monkeypatch.setattr(ls, "open_draft_pr", open_pr_mock)
    monkeypatch.setattr(ls, "add_pr_comment", AsyncMock(return_value=(True, None)))

    result = await vbp.approve_and_create_pr(
        owner="o", repo="r", base_branch="main", token="tkn",
        patch_files=[{"path": "a.css", "new_content": "body{}"}],
        commit_message="fix: retry", summary="s", bug_description="d",
        report_id="rep12345678", before_image_url=None, after_image_url=None,
        db=None, user_id="u1",
    )
    assert result["ok"] is True
    assert result["pr_url"] == "https://github.com/o/r/pull/9"
    open_pr_mock.assert_not_awaited()


# ─── 5. loop_engine.py ship-via-PR — source-level guard ─────────────
# Same convention as the other loop_engine.py invariants in
# test_iter212m115_loop_safety_five_fixes.py (that class is not
# lightly instantiable in a unit test — its neighbours use the same
# source-inspection pattern for this exact reason).
def test_loop_engine_ship_via_pr_keys_branch_on_project_not_loop_id():
    src = open("/app/backend/services/loop_engine.py").read()
    block = src.split("ship_via_pr = await _ff_is_enabled", 1)[1].split(
        "async def _fail_ship(", 1)[0]
    assert "ship_branch_name(self.loop_id)" not in block, (
        "keying the ship branch on self.loop_id (a fresh UUID every "
        "run) is the confirmed root cause of the reported duplicate "
        "ship-via-PR PRs — every run mints a new branch+PR"
    )
    assert "ship_branch_name(self.project_id" in block


def test_loop_engine_ship_via_pr_uses_centralized_reuse_helper():
    src = open("/app/backend/services/loop_engine.py").read()
    block = src.split("if ship_via_pr and pr_ship_branch:", 1)[1].split(
        "# ── Iter 272", 1)[0]
    assert "open_draft_pr" not in block, (
        "ship-via-PR must go through open_or_reuse_pr, not call "
        "open_draft_pr directly — a direct call has no dedup check"
    )
    assert "open_or_reuse_pr" in block


# ─── 6b. CI guard — prevent Bug 1 round 3 ───────────────────────────
def test_no_new_direct_open_draft_pr_call_sites_outside_loop_safety():
    """Testing-agent-suggested guard (round 2 review): the whole
    reason this bug came back is that a 2nd/3rd/4th call site called
    `open_draft_pr()` directly instead of going through the
    centralized `open_or_reuse_pr()`. Enforce it structurally — any
    FUTURE call site that does this again fails this test immediately
    instead of silently accumulating duplicates for months before
    anyone notices."""
    import subprocess
    out = subprocess.run(
        ["grep", "-rn", "open_draft_pr(",
         "/app/backend/services", "/app/backend/routers",
         "--include=*.py"],
        capture_output=True, text=True,
    ).stdout
    offenders = [
        line for line in out.splitlines()
        if "loop_safety.py" not in line
    ]
    assert not offenders, (
        "found a call site calling open_draft_pr() directly outside "
        "services/loop_safety.py — it must call open_or_reuse_pr() "
        f"instead so re-runs don't open a duplicate PR:\n" + "\n".join(offenders)
    )


# ─── 6. bot_pr_reaper — widened marker detection ────────────────────
@pytest.mark.asyncio
async def test_reaper_now_sees_visibility_kit_and_ship_via_pr_bodies(respx_mock):
    """Round-1's reaper only ever matched
    `finding_fix_applier.py`'s '**AUREM auto-fix**' body marker — it
    silently never even saw the 3 other call sites' PRs (this is
    exactly why 'Visibility Kit x5' piled up unnoticed by the
    reaper's own metric/alert too)."""
    from services import bot_pr_reaper as reaper

    respx_mock.get(f"{GH}/repos/o/r/pulls").mock(
        return_value=httpx.Response(200, json=[
            {"number": 1, "body": "**AUREM auto-fix** for todo_comment",
             "head": {"ref": "aurem/fix-todo_comment-a-py"}},
            {"number": 2, "body": "## Visibility Kit (llms_txt) — by AUREM\nAdds: ...",
             "head": {"ref": "auremcto/ship-visibility-kit-p1"}},
            {"number": 3, "body": "Opened by ORA (Loop abc123).\n\nCommit: xyz",
             "head": {"ref": "auremcto/ship-abc123"}},
            {"number": 4, "body": "_Opened automatically by AUREM's Visual Bug-Fix pipeline._",
             "head": {"ref": "aurem/visual-bugfix-rep1234"}},
            {"number": 5, "body": "unrelated human PR, no marker",
             "head": {"ref": "feature/something"}},
        ]),
    )
    out = await reaper._list_open_bot_prs(owner="o", repo="r", token="t")
    assert sorted(p["number"] for p in out) == [1, 2, 3, 4], (
        "must recognize all 4 Aurem-bot PR shapes, and correctly "
        "exclude the unrelated human PR"
    )
