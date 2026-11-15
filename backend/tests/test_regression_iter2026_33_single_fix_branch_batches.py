"""tests/test_regression_iter2026_33_single_fix_branch_batches.py

FIX_one_branch_for_all_fixes — unit tests for services/fix_batches.py:
branch naming (H6), PR body builder (cap/unicode/escaping/0 fixes),
get_or_create_open_batch (new / reuse / stale-merged / branch-deleted
/ lookup-failure), the 50-fix cap rollover, and the per-(user,project)
lock.

GitHub is mocked at the services.loop_safety helper boundary
(create_or_reuse_branch / open_or_reuse_pr / add_pr_label /
get_pr_status / update_pr_body) — same "mock transport" convention as
the existing tests/test_iter212m114_real_finding_fix.py. Mongo is the
repo's hand-rolled dict-fake-DB pattern (no real local Mongo needed
for these unit tests).
"""
from __future__ import annotations

import asyncio
import re

import pytest

import services.fix_batches as fb
import services.loop_safety as ls

pytestmark = pytest.mark.asyncio


# ───────────────────────────── fake DB double ────────────────────────────
class _FakeFixBatches:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.insert_calls = 0
        self.force_duplicate_key_once = False

    def _match(self, r, filt):
        for k, v in filt.items():
            if r.get(k) != v:
                return False
        return True

    async def find_one(self, filt, proj=None):
        for r in self.rows.values():
            if self._match(r, filt):
                return dict(r)
        return None

    async def find_one_and_update(self, filt, update, return_document=False):
        for r in self.rows.values():
            if self._match(r, filt):
                for k, v in update.get("$inc", {}).items():
                    r[k] = r.get(k, 0) + v
                for k, v in update.get("$push", {}).items():
                    r.setdefault(k, []).append(v)
                for k, v in update.get("$set", {}).items():
                    r[k] = v
                return dict(r)
        return None

    async def update_one(self, filt, update):
        for r in self.rows.values():
            if self._match(r, filt):
                r.update(update.get("$set", {}))
                return
        return

    async def insert_one(self, row):
        self.insert_calls += 1
        if self.force_duplicate_key_once:
            self.force_duplicate_key_once = False
            raise RuntimeError("E11000 duplicate key error")
        self.rows[row["batch_id"]] = dict(row)


class _FakeDB:
    def __init__(self):
        self.fix_batches = _FakeFixBatches()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture(autouse=True)
def _reset_locks():
    fb._batch_locks.clear()
    yield
    fb._batch_locks.clear()


def _mock_github_success(monkeypatch, *, pr_number=101, pr_url="https://github.com/o/r/pull/101"):
    async def _create_branch(**kw):
        return True, None

    async def _open_pr(**kw):
        return {"ok": True, "pr_url": pr_url, "pr_number": pr_number, "reused": False, "error": None}

    async def _add_label(**kw):
        return True, None

    monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)
    monkeypatch.setattr(ls, "open_or_reuse_pr", _open_pr)
    monkeypatch.setattr(ls, "add_pr_label", _add_label)


# ───────────────────────── H6 — branch naming ────────────────────────────
class TestBranchNaming:
    def test_not_derived_from_rule_or_path(self):
        b1 = fb.generate_batch_branch()
        b2 = fb.generate_batch_branch()
        assert b1 != b2  # no deterministic collision across calls
        assert b1.startswith("aurem/fixes-")

    def test_never_matches_reaper_trailing_timestamp_regex(self):
        """bot_pr_reaper strips a trailing `-\\d{9,}` segment when
        grouping duplicates — an 8-hex suffix must never satisfy that
        (even in the all-digit-looking worst case, 8 < 9)."""
        trailing_ts_re = re.compile(r"-\d{9,}$")
        for _ in range(200):
            branch = fb.generate_batch_branch()
            assert not trailing_ts_re.search(branch), branch

    def test_suffix_is_exactly_8_hex_chars(self):
        branch = fb.generate_batch_branch()
        suffix = branch.rsplit("-", 1)[-1]
        assert len(suffix) == 8
        assert re.fullmatch(r"[0-9a-f]{8}", suffix)


# ───────────────────────── PR body builder ───────────────────────────────
class TestBuildPrBody:
    def test_zero_fixes(self):
        body = fb.build_pr_body([])
        assert "Rule" in body
        assert len(body) < 65536

    def test_renders_all_rows_under_cap(self):
        rows = [
            {"rule_id": f"rule_{i}", "file": f"f{i}.py", "line": i,
             "severity": "high", "commit_sha": "a" * 40, "status": "fixed"}
            for i in range(10)
        ]
        body = fb.build_pr_body(rows)
        for i in range(10):
            assert f"rule_{i}" in body
        assert len(body) < 65536

    def test_huge_row_count_stays_under_github_cap(self):
        rows = [
            {"rule_id": f"rule_{i}", "file": f"some/long/path/to/file_{i}.py",
             "line": i, "severity": "medium", "commit_sha": "b" * 40, "status": "fixed"}
            for i in range(2000)
        ]
        body = fb.build_pr_body(rows)
        assert len(body) < 65536
        assert "omitted" in body

    def test_unicode_in_rule_and_file_names(self):
        rows = [{"rule_id": "règle_é", "file": "résumé/文件.py", "line": 1,
                 "severity": "low", "commit_sha": "c" * 40, "status": "fixed"}]
        body = fb.build_pr_body(rows)
        assert "résumé" in body
        assert "文件.py" in body

    def test_pipe_character_does_not_break_the_table(self):
        rows = [{"rule_id": "rule|with|pipes", "file": "f.py", "line": 1,
                 "severity": "high", "commit_sha": "d" * 40, "status": "fixed"}]
        body = fb.build_pr_body(rows)
        # Escaped pipes, not raw ones that would add table columns.
        assert "rule\\|with\\|pipes" in body

    def test_mention_is_defused_inside_backticks(self):
        rows = [{"rule_id": "@octocat made this up", "file": "f.py", "line": 1,
                 "severity": "high", "commit_sha": "e" * 40, "status": "fixed"}]
        body = fb.build_pr_body(rows)
        assert "`@octocat`" in body
        assert body.count("@octocat") <= 1  # only inside the backtick-wrapped form

    def test_none_line_renders_as_question_mark(self):
        rows = [{"rule_id": "r", "file": "f.py", "line": None,
                 "severity": "low", "commit_sha": "f" * 40, "status": "fixed"}]
        body = fb.build_pr_body(rows)
        assert "f.py:?" in body


# ──────────────────── get_or_create_open_batch — unit ────────────────────
class TestGetOrCreateOpenBatch:
    async def test_invalid_user_id_rejected(self):
        db = _FakeDB()
        out = await fb.get_or_create_open_batch(
            db, user_id="", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is False

    async def test_invalid_project_id_rejected(self):
        db = _FakeDB()
        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id=None, owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is False

    async def test_operator_injection_in_user_id_rejected(self):
        db = _FakeDB()
        out = await fb.get_or_create_open_batch(
            db, user_id={"$ne": None}, project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is False

    async def test_creates_new_batch_when_none_open(self, monkeypatch):
        _mock_github_success(monkeypatch)
        db = _FakeDB()
        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is True
        assert out["created"] is True
        assert out["batch"]["branch"].startswith("aurem/fixes-")
        assert out["batch"]["status"] == "open"
        assert db.fix_batches.insert_calls == 1

    async def test_reuses_open_batch_when_github_confirms_open(self, monkeypatch):
        async def _get_status(**kw):
            return {"ok": True, "merged": False, "state": "open", "merge_commit_sha": None}
        monkeypatch.setattr(ls, "get_pr_status", _get_status)
        db = _FakeDB()
        db.fix_batches.rows["fb_1"] = {
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "base_branch": "main",
            "pr_number": 5, "pr_url": "https://github.com/o/r/pull/5",
            "status": "open", "fix_count": 2,
        }
        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is True
        assert out["created"] is False
        assert out["batch"]["batch_id"] == "fb_1"

    async def test_stale_merged_batch_triggers_new_one(self, monkeypatch):
        async def _get_status(**kw):
            return {"ok": True, "merged": True, "state": "closed", "merge_commit_sha": "abc"}
        monkeypatch.setattr(ls, "get_pr_status", _get_status)
        _mock_github_success(monkeypatch, pr_number=202)
        db = _FakeDB()
        db.fix_batches.rows["fb_1"] = {
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "status": "open", "fix_count": 3,
            "pr_number": 5, "pr_url": "https://github.com/o/r/pull/5",
        }
        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is True
        assert out["created"] is True
        assert db.fix_batches.rows["fb_1"]["status"] == "merged"
        assert out["batch"]["batch_id"] != "fb_1"

    async def test_stale_closed_batch_triggers_new_one(self, monkeypatch):
        async def _get_status(**kw):
            return {"ok": True, "merged": False, "state": "closed", "merge_commit_sha": None}
        monkeypatch.setattr(ls, "get_pr_status", _get_status)
        _mock_github_success(monkeypatch, pr_number=303)
        db = _FakeDB()
        db.fix_batches.rows["fb_1"] = {
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "status": "open", "fix_count": 1,
            "pr_number": 5, "pr_url": "https://github.com/o/r/pull/5",
        }
        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["created"] is True
        assert db.fix_batches.rows["fb_1"]["status"] == "closed"

    async def test_lookup_failure_is_retryable_not_a_new_batch(self, monkeypatch):
        """A GitHub network blip must NEVER silently mint a second
        open batch — this is the specific correctness gap the design
        calls out."""
        async def _get_status(**kw):
            return {"ok": False, "merged": False, "state": "unknown", "merge_commit_sha": None}
        monkeypatch.setattr(ls, "get_pr_status", _get_status)
        db = _FakeDB()
        db.fix_batches.rows["fb_1"] = {
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "status": "open", "fix_count": 1,
            "pr_number": 5, "pr_url": "https://github.com/o/r/pull/5",
        }
        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is False
        assert out["error"] == "batch_verify_failed_retryable"
        assert db.fix_batches.rows["fb_1"]["status"] == "open"  # untouched
        assert db.fix_batches.insert_calls == 0  # no second batch created

    async def test_branch_create_failure_is_hard_error_no_fallback(self, monkeypatch):
        """H2 — no direct-to-base fallback anywhere."""
        async def _create_branch(**kw):
            return False, "base_ref_status_404"
        monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)
        db = _FakeDB()
        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is False
        assert out["error"] == "base_ref_status_404"
        assert db.fix_batches.insert_calls == 0

    async def test_duplicate_key_reads_the_winner(self, monkeypatch):
        """H4 cross-instance backstop: a duplicate-key error on insert
        means another process won the race — re-read ITS row."""
        _mock_github_success(monkeypatch)
        db = _FakeDB()
        db.fix_batches.force_duplicate_key_once = True
        winner = {
            "batch_id": "fb_winner", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-bbbbbbbb", "status": "open", "fix_count": 0,
            "pr_number": 9, "pr_url": "https://github.com/o/r/pull/9",
        }

        # First lookup (no existing batch) must return None so we
        # attempt to create; the simulated race then surfaces the
        # "winner" on the duplicate-key re-read.
        calls = {"n": 0}

        async def _find_one_seq(filt, proj=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return winner
        monkeypatch.setattr(db.fix_batches, "find_one", _find_one_seq)

        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is True
        assert out["created"] is False
        assert out["batch"]["batch_id"] == "fb_winner"

    async def test_cap_reached_closes_batch_and_starts_new_one(self, monkeypatch):
        _mock_github_success(monkeypatch, pr_number=404)
        db = _FakeDB()
        db.fix_batches.rows["fb_full"] = {
            "batch_id": "fb_full", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-cccccccc", "status": "open",
            "fix_count": fb.MAX_FIXES_PER_BATCH,
            "pr_number": 5, "pr_url": "https://github.com/o/r/pull/5",
        }
        out = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["created"] is True
        assert db.fix_batches.rows["fb_full"]["status"] == "closed"
        assert db.fix_batches.rows["fb_full"]["close_reason"] == "cap_reached"

    async def test_cross_user_never_shares_a_batch(self, monkeypatch):
        _mock_github_success(monkeypatch, pr_number=11)
        db = _FakeDB()
        db.fix_batches.rows["fb_a"] = {
            "batch_id": "fb_a", "user_id": "userA", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "status": "open", "fix_count": 0,
            "pr_number": 5, "pr_url": "https://github.com/o/r/pull/5",
        }
        out = await fb.get_or_create_open_batch(
            db, user_id="userB", project_id="p1", owner="o", repo="r",
            base_branch="main", token="tok",
        )
        assert out["ok"] is True
        assert out["batch"]["batch_id"] != "fb_a"
        assert out["batch"]["user_id"] == "userB"


# ───────────────────────────── lock behavior ──────────────────────────────
class TestLock:
    async def test_lock_serializes_concurrent_creates_to_one_batch(self, monkeypatch):
        """Two 'simultaneous' get_or_create calls for the same
        (user, project) must still end up with exactly ONE open batch
        — the in-process lock (H4)."""
        call_count = {"n": 0}

        async def _create_branch(**kw):
            call_count["n"] += 1
            await asyncio.sleep(0.01)  # widen the race window
            return True, None

        async def _open_pr(**kw):
            return {"ok": True, "pr_url": "https://github.com/o/r/pull/77",
                     "pr_number": 77, "reused": False, "error": None}

        async def _add_label(**kw):
            return True, None

        async def _get_status(**kw):
            # Reached only by the SECOND caller, after the first has
            # already inserted its batch — confirms it as still open
            # rather than hitting the real network.
            return {"ok": True, "merged": False, "state": "open", "merge_commit_sha": None}

        monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)
        monkeypatch.setattr(ls, "open_or_reuse_pr", _open_pr)
        monkeypatch.setattr(ls, "add_pr_label", _add_label)
        monkeypatch.setattr(ls, "get_pr_status", _get_status)

        db = _FakeDB()
        results = await asyncio.gather(
            fb.get_or_create_open_batch(db, user_id="u1", project_id="p1",
                                         owner="o", repo="r", base_branch="main", token="t"),
            fb.get_or_create_open_batch(db, user_id="u1", project_id="p1",
                                         owner="o", repo="r", base_branch="main", token="t"),
        )
        assert all(r["ok"] for r in results)
        assert db.fix_batches.insert_calls == 1
        assert call_count["n"] == 1  # branch only ever created once


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
