"""
tests/test_graph_refresh_after_commit_2026_09_17.py

Founder-reported: Codebase Graph stayed stale (5.5 days old) despite
several real same-day commits. Root cause: the staleness-check-and-
rebuild ("if the graph is >30 min old, rebuild silently") only ever
lived inline inside `_generate_plan` (services/loop_engine.py) — the
full multi-step PLANNING loop. A simple one-shot chat fix (the git-
binary worker in worker_git.py, or the GitHub-API worker in
worker_api.py) never goes through `_generate_plan`, so its commits
never triggered a graph rebuild at all.

Fix: extracted the same check into
`services.graph_builder.refresh_graph_after_commit`, now called
(fire-and-forget) from both one-shot commit workers too.
"""
import time
from unittest.mock import AsyncMock, patch

import pytest

from services.graph_builder import refresh_graph_after_commit


class _FakeCollection:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, _q, _proj):
        return self._doc


class _FakeDB:
    def __init__(self, doc):
        self.project_graphs = _FakeCollection(doc)


@pytest.mark.asyncio
async def test_never_built_before_triggers_a_build():
    db = _FakeDB(None)
    with patch("services.graph_builder.build_graph", new=AsyncMock()) as mock_build:
        await refresh_graph_after_commit(
            db=db, project_id="p1", user_id="u1", gh_token="tok",
            gh_owner="o", gh_repo="r", branch="main",
        )
    mock_build.assert_awaited_once()


@pytest.mark.asyncio
async def test_fresh_graph_under_30_minutes_does_not_rebuild():
    db = _FakeDB({"built_at": time.time() - 60})  # 1 minute old
    with patch("services.graph_builder.build_graph", new=AsyncMock()) as mock_build:
        await refresh_graph_after_commit(
            db=db, project_id="p1", user_id="u1", gh_token="tok",
            gh_owner="o", gh_repo="r", branch="main",
        )
    mock_build.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_graph_over_30_minutes_rebuilds():
    # Exact scenario reported: graph is days old, real commit just
    # happened via a one-shot worker — this must now trigger a rebuild.
    db = _FakeDB({"built_at": time.time() - 5.5 * 24 * 3600})  # 5.5 days
    with patch("services.graph_builder.build_graph", new=AsyncMock()) as mock_build:
        await refresh_graph_after_commit(
            db=db, project_id="p1", user_id="u1", gh_token="tok",
            gh_owner="o", gh_repo="r", branch="main",
        )
    mock_build.assert_awaited_once_with(
        db=db, project_id="p1", user_id="u1", gh_token="tok",
        gh_owner="o", gh_repo="r", branch="main",
    )


@pytest.mark.asyncio
async def test_missing_required_args_is_a_safe_noop():
    db = _FakeDB(None)
    with patch("services.graph_builder.build_graph", new=AsyncMock()) as mock_build:
        await refresh_graph_after_commit(
            db=db, project_id="p1", user_id="u1", gh_token="",  # no token
            gh_owner="o", gh_repo="r", branch="main",
        )
    mock_build.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_graph_exception_is_swallowed_never_raises():
    db = _FakeDB(None)
    with patch(
        "services.graph_builder.build_graph",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        # Must not raise — a graph-refresh failure can never affect a
        # task that already shipped successfully.
        await refresh_graph_after_commit(
            db=db, project_id="p1", user_id="u1", gh_token="tok",
            gh_owner="o", gh_repo="r", branch="main",
        )


@pytest.mark.asyncio
async def test_none_db_is_a_safe_noop():
    with patch("services.graph_builder.build_graph", new=AsyncMock()) as mock_build:
        await refresh_graph_after_commit(
            db=None, project_id="p1", user_id="u1", gh_token="tok",
            gh_owner="o", gh_repo="r", branch="main",
        )
    mock_build.assert_not_awaited()


# ─── Wiring lock-in: both one-shot commit workers must call the
# shared helper right after marking the task "done", so a real commit
# through either path actually triggers this check. Source-level
# assertion (matching the existing convention in
# test_iter52_production_bug_fixes.py) — avoids needing to simulate a
# real `git`/GitHub-API commit end-to-end just to prove the wiring.

def test_git_worker_calls_refresh_after_done_status():
    src = open("routers/cto_projects/worker_git.py").read()
    idx = src.index('status="done", result=summary,\n                          commit_sha=sha,\n                          files_changed_simple=list(edits.keys()),\n                          edits=_frontend_subset(edits),\n                          completed_at=time.time())')
    tail = src[idx:idx + 900]
    assert "refresh_graph_after_commit" in tail
    assert "asyncio.create_task" in tail


def test_api_worker_calls_refresh_after_done_status():
    src = open("routers/cto_projects/worker_api.py").read()
    idx = src.index('status="done", result=summary,\n                          commit_sha=sha,\n                          files_changed_simple=list(edits.keys()),\n                          edits=_frontend_subset(edits),\n                          verified=True,')
    tail = src[idx:idx + 900]
    assert "refresh_graph_after_commit" in tail
    assert "asyncio.create_task" in tail
