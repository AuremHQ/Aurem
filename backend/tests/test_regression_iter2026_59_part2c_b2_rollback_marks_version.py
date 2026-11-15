"""tests/test_regression_iter2026_59_part2c_b2_rollback_marks_version.py

Part 2c B2 — "if an existing single function already reverts a
merged ship (rollback_manager / R10 one-click), mark that version
rolled_back there; do not build new rollback logic." That function is
services/loop_rollback.py::run_rollback (shared by the G12 admin path
AND the R10 user one-click path). Its fix-batch equivalent is
services/finding_fix_applier.py::revert_batch (already rewritten in
Part 2c C1/C2 of this same task). Both must mark the corresponding
project_versions row `rolled_back` on a SUCCESSFUL revert — no new
rollback mechanism, just this one extra call at the existing success
point.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class TestRunRollbackMarksVersionRolledBack:
    async def test_successful_ship_rollback_marks_the_version_row(self, monkeypatch):
        from services import loop_rollback
        from tests.test_iter367_rollback_fake_success_fix import _MemDB

        db = _MemDB()
        db.loop_sessions.rows.append({
            "loop_id": "l1", "user_id": "u1",
            "context": {"commit": {"full_sha": "a" * 40}},
        })
        project = {"project_id": "p1", "user_id": "u1",
                   "github_owner": "o", "github_repo": "r", "branch": "main"}

        async def _revert_commit(**kw):
            return {"ok": True, "sha": "rev0000", "full_sha": "rev0000" + "0" * 33,
                    "html_url": "https://x"}

        async def _verify(owner, repo, branch, expected, token, **kw):
            return {"verified": True, "last_sha": expected}

        monkeypatch.setattr("services.github_api_writer.revert_commit", _revert_commit)
        monkeypatch.setattr("services.github_api_writer.verify_branch_head", _verify)

        mark_calls = []

        async def _fake_mark(db_, **kw):
            mark_calls.append(kw)
        monkeypatch.setattr(loop_rollback, "mark_rolled_back", _fake_mark)

        await loop_rollback.run_rollback(
            db, "l1", project, "a" * 40, "ghs_TOKEN",
        )
        sess = await db.loop_sessions.find_one({"loop_id": "l1"})
        assert sess["rollback_status"] == "done"
        assert mark_calls == [{"loop_id": "l1"}]

    async def test_failed_verification_never_marks_the_version_row(self, monkeypatch):
        from services import loop_rollback
        from tests.test_iter367_rollback_fake_success_fix import _MemDB

        db = _MemDB()
        db.loop_sessions.rows.append({
            "loop_id": "l1", "user_id": "u1",
            "context": {"commit": {"full_sha": "a" * 40}},
        })
        project = {"project_id": "p1", "user_id": "u1",
                   "github_owner": "o", "github_repo": "r", "branch": "main"}

        async def _revert_commit(**kw):
            return {"ok": True, "sha": "rev0000", "full_sha": "rev0000" + "0" * 33, "html_url": "x"}

        async def _verify(owner, repo, branch, expected, token, **kw):
            return {"verified": False, "last_sha": "something_else"}

        monkeypatch.setattr("services.github_api_writer.revert_commit", _revert_commit)
        monkeypatch.setattr("services.github_api_writer.verify_branch_head", _verify)

        mark_calls = []

        async def _fake_mark(db_, **kw):
            mark_calls.append(kw)
        monkeypatch.setattr(loop_rollback, "mark_rolled_back", _fake_mark)

        await loop_rollback.run_rollback(db, "l1", project, "a" * 40, "ghs_TOKEN")
        sess = await db.loop_sessions.find_one({"loop_id": "l1"})
        assert sess["rollback_status"] == "failed"
        assert mark_calls == [], (
            "a revert that could not be VERIFIED landed must never mark "
            "the version row rolled_back — that would be a false claim."
        )


class TestRevertBatchMarksVersionRolledBack:
    async def test_successful_batch_revert_marks_the_version_row(self, monkeypatch):
        import services.finding_fix_applier as ffa

        class _FakeColl:
            def __init__(self, docs=None):
                self.docs = docs or []

            async def find_one(self, filt, proj=None):
                for d in self.docs:
                    if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                        return dict(d)
                return None

            async def update_one(self, filt, update):
                for d in self.docs:
                    if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                        d.update(update.get("$set", {}))
                        return

            async def update_many(self, filt, update):
                for d in self.docs:
                    if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict)):
                        d.update(update.get("$set", {}))

            def find(self, filt, proj=None):
                matched = [dict(d) for d in self.docs
                           if all(d.get(k) == v for k, v in (filt or {}).items() if not isinstance(v, dict))]

                class _Cur:
                    def __init__(self, items):
                        self.items = items

                    async def to_list(self, length=None):
                        return list(self.items)
                return _Cur(matched)

        class _FakeDB:
            def __init__(self):
                self.cto_projects = _FakeColl([{
                    "project_id": "p1", "user_id": "u1",
                    "github_owner": "o", "github_repo": "r",
                }])
                self.fix_batches = _FakeColl([{
                    "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
                    "branch": "aurem/fixes-aaaaaaaa", "pr_number": 5, "status": "open",
                }])
                self.finding_fixes = _FakeColl([
                    {"fix_id": "fx_1", "batch_id": "fb_1"},
                ])

            def __getitem__(self, name):
                return getattr(self, name)

        db = _FakeDB()

        async def _close_pr(**kw):
            return True, None

        async def _delete_branch(**kw):
            return False, "irrelevant"
        monkeypatch.setattr("services.loop_safety.close_pr", _close_pr)
        monkeypatch.setattr("services.loop_safety.delete_ship_branch", _delete_branch)

        mark_calls = []

        async def _fake_mark(db_, **kw):
            mark_calls.append(kw)
        monkeypatch.setattr(ffa, "mark_rolled_back", _fake_mark)

        result = await ffa.revert_batch(
            db, batch_id="fb_1", user_id="u1", project_id="p1", token="tok",
        )
        assert result["ok"] is True
        assert mark_calls == [{"batch_id": "fb_1"}]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
