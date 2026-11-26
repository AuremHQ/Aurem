"""
Part XIII · 3(b) + 3(c) + 2(a) — ship path, real LoopEngine.confirm_ship.

3(b) FAIL-CLOSED snapshot: if create_snapshot raises (or returns no
    snapshot_id), the ship aborts BEFORE commit_files — no commit is
    created, nothing is pushed, state FAILED with a clear error.
3(c) PR failure surfacing: with ship_via_pr ON and open_or_reuse_pr
    failing, the ship still COMPLETES (the commit landed) but pr_err
    is recorded on context["commit"], on the terminal COMPLETED frame
    and in the ship narration — never a plain "Shipped {sha}".
2(a) Mode threading: LoopEngine(mode=...) stores + persists it, and
    lookup_or_rehydrate restores it from the session doc.

Real confirm_ship() with a fake in-memory DB (the T1 pattern from
test_iter_ship_commit_robustness.py) and patched GitHub/flag/snapshot
boundaries. No live GitHub calls, no live Mongo.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, patch

# Import the patched modules first so unittest.mock.patch can resolve
# their attributes (services/__init__.py does not import submodules).
import services.feature_flags  # noqa: F401
import services.founder_alerts  # noqa: F401
import services.git_identity  # noqa: F401
import services.github_api_writer  # noqa: F401
import services.loop_safety  # noqa: F401
import services.project_brain  # noqa: F401
import services.rollback_snapshot  # noqa: F401
import services.ship_verification_audit  # noqa: F401
from services import loop_engine as le
from services.ora_chat_v2 import llm_client  # noqa: F401

SHIP_SHA = "deadbeef1234567890abcdef0000111122223333"


class _Coll:
    def __init__(self, find_one=None, find_one_and_update=None):
        self.rows: list[dict] = []
        self._find_one = find_one
        self._foua = find_one_and_update

    async def insert_one(self, d):
        self.rows.append(dict(d))

    async def update_one(self, q, u, upsert=False):
        pass

    async def find_one(self, q, *a, **kw):
        if self._find_one is not None:
            return await self._find_one(q, *a, **kw)
        return None

    async def find_one_and_update(self, q, u, **kw):
        if self._foua is not None:
            return await self._foua(q, u, **kw)
        return None

    async def delete_one(self, q):
        pass


class _DB:
    """Fake Mongo. cto_projects.find_one is projection-aware: the
    token-refresh query (auth_method projection) returns None so the
    pending token is kept; the pin query returns the live binding."""

    def __init__(self, session_doc=None, claim=True):
        self._session_doc = session_doc
        self._claim = claim
        self.loop_sessions = _Coll(
            find_one=self._ls_find_one,
            find_one_and_update=self._ls_foua,
        )
        self.loop_backups = _Coll()
        self.loop_plans = _Coll()
        self.loop_lock = _Coll()
        self.loop_failures = _Coll()
        self.ship_pr_events = _Coll()
        self.cto_projects = _Coll(find_one=self._proj_find_one)
        self.dev_users = _Coll()
        self.chat_sessions = _Coll()

    def __getattr__(self, name):
        coll = _Coll()
        setattr(self, name, coll)
        return coll

    async def _ls_find_one(self, q, *a, **kw):
        return self._session_doc

    async def _ls_foua(self, q, u, **kw):
        return {"loop_id": "lp_p13"} if self._claim else None

    async def _proj_find_one(self, q, *a, **kw):
        proj = {}
        if a:
            proj = a[0] or {}
        elif "projection" in kw:
            proj = kw["projection"] or {}
        if "auth_method" in proj:
            return None  # skip token re-mint
        return {
            "github_owner": "acme", "github_repo": "widgets",
            "github_branch": "main", "installation_id": 123,
        }


def _engine(db, loop_id="lp_p13", **kw):
    eng = le.LoopEngine(
        db=db, loop_id=loop_id, user_id="u1",
        project_id="p1", user_message="ship it", **kw,
    )
    eng.state = le.LoopState.PAUSED_FOR_USER
    eng.phase = "ship"
    eng.context["ship_pending"] = {
        "owner": "acme", "repo": "widgets", "branch": "main",
        "token": "tok",
        "files": {"app.py": "print('hi')\n"},
        "commit_message": "feat: ship test",
        "files_diff": [],
    }
    return eng


def _drain_frames(eng):
    frames = []
    while not eng.queue.empty():
        frames.append(eng.queue.get_nowait())
    return frames


def _patches(*, ship_via_pr, snapshot=None, snapshot_err=None,
             commit_files=None, pr_result=None, pr_side_effect=None):
    """Common patch stack for confirm_ship tests. Returns the
    commit_files mock so callers can assert on it."""
    commit_mock = AsyncMock(return_value={
        "ok": True, "sha": SHIP_SHA[:7], "full_sha": SHIP_SHA,
        "html_url": f"https://github.com/acme/widgets/commit/{SHIP_SHA}",
    })
    if commit_files is not None:
        commit_mock = commit_files
    stack = [
        patch("services.ora_chat_v2.llm_client.is_mock",
              return_value=False),
        patch("services.feature_flags.is_enabled",
              AsyncMock(return_value=ship_via_pr)),
        patch("services.rollback_snapshot.create_snapshot",
              AsyncMock(side_effect=snapshot_err)
              if snapshot_err is not None else
              AsyncMock(return_value=snapshot if snapshot is not None
                        else {"snapshot_id": "snap_123"})),
        patch("services.founder_alerts.send_founder_alert",
              AsyncMock()),
        patch("services.git_identity.resolve_git_identity",
              AsyncMock(return_value=("AUREM", "aurem@example.com"))),
        patch("services.git_identity.build_commit_message",
              return_value="feat: ship test"),
        patch("services.github_api_writer.commit_files", commit_mock),
        # Background post-ship tasks: keep them stubbed so nothing
        # touches the network after the test's event loop closes.
        patch("services.ship_verification_audit.verify_shipped_commit",
              AsyncMock(return_value={})),
        patch("services.project_brain.update_brain_after_commit",
              AsyncMock()),
        patch("services.project_brain.update_brain_after_task",
              AsyncMock()),
    ]
    if pr_result is not None or pr_side_effect is not None:
        stack.append(patch(
            "services.loop_safety.open_or_reuse_pr",
            AsyncMock(side_effect=pr_side_effect)
            if pr_side_effect is not None else
            AsyncMock(return_value=pr_result)))
        stack.append(patch(
            "services.loop_safety.create_or_reuse_branch",
            AsyncMock(return_value=(True, None))))
        stack.append(patch(
            "services.loop_safety.add_pr_label",
            AsyncMock(return_value=None)))
    return stack, commit_mock


def _run_with_patches(eng, stack):
    async def _run():
        for p in stack:
            p.__enter__()
        try:
            await eng.confirm_ship(True)
        finally:
            for p in reversed(stack):
                p.__exit__(None, None, None)
    asyncio.run(_run())


# ── 3(b) ─────────────────────────────────────────────────────────────

def test_3b_snapshot_failure_blocks_push():
    """create_snapshot raises → fail-closed: commit_files NEVER runs,
    no commit exists, state FAILED, error names the snapshot."""
    eng = _engine(_DB())
    stack, commit_mock = _patches(
        ship_via_pr=False, snapshot_err=RuntimeError("R2 down"))
    _run_with_patches(eng, stack)
    commit_mock.assert_not_called()
    assert eng.state == le.LoopState.FAILED
    assert eng.context["pre_ship_snapshot_ok"] is False
    assert "snapshot" in eng.context["commit"]["error"].lower()
    # pre_ship_snapshot_error holds repr(exception) — the R2 cause.
    assert "r2 down" in eng.context["pre_ship_snapshot_error"].lower()
    frames = _drain_frames(eng)
    failed = [f for f in frames if f.get("state") == "failed"]
    assert failed, "expected a FAILED terminal frame"
    assert "snapshot" in (failed[-1].get("message") or "").lower()


def test_3b_snapshot_without_id_blocks_push():
    """create_snapshot returning a row with no snapshot_id is the
    same fail-closed abort — a snapshot row without an id is not a
    valid rollback snapshot."""
    eng = _engine(_DB())
    stack, commit_mock = _patches(
        ship_via_pr=False, snapshot={})
    _run_with_patches(eng, stack)
    commit_mock.assert_not_called()
    assert eng.state == le.LoopState.FAILED
    assert eng.context["pre_ship_snapshot_ok"] is False


def test_3b_snapshot_success_allows_push():
    """Control: a valid snapshot lets the ship through to
    commit_files and COMPLETED."""
    eng = _engine(_DB())
    stack, commit_mock = _patches(ship_via_pr=False)
    _run_with_patches(eng, stack)
    commit_mock.assert_called_once()
    assert eng.state == le.LoopState.COMPLETED
    assert eng.context["pre_ship_snapshot_ok"] is True
    assert eng.context["commit"]["full_sha"] == SHIP_SHA


# ── 3(c) ─────────────────────────────────────────────────────────────

def test_3c_pr_failure_still_ships_and_surfaces_pr_err():
    """ship_via_pr ON, open_or_reuse_pr returns ok=False + error:
    the commit still ships (state COMPLETED) AND pr_err is visible
    on context["commit"], the terminal COMPLETED frame and the
    narration — not a silent plain 'Shipped {sha}'."""
    eng = _engine(_DB())
    stack, commit_mock = _patches(
        ship_via_pr=True,
        pr_result={"ok": False, "error": "boom"},
    )
    _run_with_patches(eng, stack)
    commit_mock.assert_called_once()
    assert eng.state == le.LoopState.COMPLETED
    assert eng.context["commit"]["pr_error"] == "boom"
    assert eng.context["commit"]["full_sha"] == SHIP_SHA
    assert eng.context["commit"]["pr_url"] is None

    frames = _drain_frames(eng)
    completed = [f for f in frames if f.get("state") == "completed"]
    assert completed, "expected a COMPLETED terminal frame"
    terminal = completed[-1]
    assert (terminal.get("data") or {}).get("pr_error") == "boom"
    narrations = [f for f in frames
                  if (f.get("data") or {}).get("type") == "narration"]
    ship_narration = [n for n in narrations
                      if "PR could not be opened" in
                      ((n.get("data") or {}).get("narration_text") or "")]
    assert ship_narration, "narration must say the PR could not be opened"
    assert ((ship_narration[-1].get("data") or {}).get("pr_error")) == "boom"


def test_3c_pr_exception_still_ships_and_surfaces_pr_err():
    """open_or_reuse_pr RAISING (not just returning ok=False) is the
    same story: ship completes, pr_err recorded."""
    eng = _engine(_DB())
    stack, commit_mock = _patches(
        ship_via_pr=True,
        pr_side_effect=RuntimeError("GitHub 502"),
    )
    _run_with_patches(eng, stack)
    assert eng.state == le.LoopState.COMPLETED
    assert "GitHub 502" in (eng.context["commit"]["pr_error"] or "")


def test_3c_pr_success_has_no_pr_err():
    """Control: a PR that opens keeps pr_error None and the narration
    is the PR-opened copy."""
    eng = _engine(_DB())
    stack, commit_mock = _patches(
        ship_via_pr=True,
        pr_result={"ok": True, "pr_url": "https://github.com/acme/widgets/pull/7",
                   "pr_number": 7, "reused": False},
    )
    _run_with_patches(eng, stack)
    assert eng.state == le.LoopState.COMPLETED
    assert eng.context["commit"]["pr_error"] is None
    assert eng.context["commit"]["pr_url"] == \
        "https://github.com/acme/widgets/pull/7"
    frames = _drain_frames(eng)
    completed = [f for f in frames if f.get("state") == "completed"]
    assert (completed[-1].get("data") or {}).get("pr_error") is None
    narrations = [f for f in frames
                  if (f.get("data") or {}).get("type") == "narration"]
    assert any("PR opened for" in ((n.get("data") or {}).get("narration_text") or "")
               for n in narrations)


# ── 2(a) — mode threading at the engine level ────────────────────────

def test_2a_engine_stores_mode_and_persists_it_in_doc():
    eng = _engine(_DB(), mode="maxx")
    assert eng.mode == "maxx"
    assert eng._doc()["mode"] == "maxx"


def test_2a_engine_normalises_mode_case():
    eng = _engine(_DB(), mode="PRO")
    assert eng.mode == "pro"
    assert eng._doc()["mode"] == "pro"


def test_2a_engine_mode_none_is_full_council():
    eng = _engine(_DB())
    assert eng.mode is None
    assert eng._doc()["mode"] is None


def test_2a_rehydrate_restores_mode_from_session_doc():
    """lookup_or_rehydrate rebuilds the engine from the persisted
    session doc — mode survives the round-trip."""
    le.reset_registry()
    doc = {
        "loop_id": "lp_p13_re", "user_id": "u1", "project_id": "p1",
        "state": "paused_for_user", "phase": "ship", "mode": "pro",
        "context": {"original_request": "ship it"},
    }
    db = _DB(session_doc=doc)
    eng = asyncio.run(le.lookup_or_rehydrate(db, "lp_p13_re"))
    assert eng is not None
    assert eng.mode == "pro"
    assert eng.state == le.LoopState.PAUSED_FOR_USER
    le.reset_registry()
