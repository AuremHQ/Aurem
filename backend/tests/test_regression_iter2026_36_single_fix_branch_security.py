"""tests/test_regression_iter2026_36_single_fix_branch_security.py

FIX_one_branch_for_all_fixes — security surface.

  * IDOR: get_batch_by_id / revert_batch are ALWAYS scoped by
    (batch_id, user_id, project_id) together — batch_id alone must
    never resolve another user's or another project's batch.
  * Mongo operator injection: get_or_create_open_batch rejects a
    dict-shaped user_id/project_id (e.g. {"$ne": None}) outright,
    never lets it reach a query.
  * Webhook forgery/replay: dispatch_pull_request_webhook's new
    `aura:fix-batch` branch —
      - a non-string `head.ref` (operator-injection-shaped JSON) is
        coerced to "" and never reaches a Mongo filter as a dict
        (regression for the hardening added alongside this test).
      - replaying the SAME merged-PR payload twice is idempotent:
        second delivery doesn't double-merge, error, or re-fire the
        brain-outcome hook in a way that corrupts state.
  * No raw exception/secret leak: apply_finding_fix's error returns
    never carry the GitHub token.
  * bot_pr_reaper safety (H5, design item 8): a shared-batch PR's body
    contains NONE of bot_pr_reaper.AUREM_BOT_MARKERS, so it is never
    even picked up by `_list_open_bot_prs` — never closed as a
    duplicate, never grouped with another batch's PR.

Mongo via the repo's dict-fake-DB pattern; GitHub via mock transport.
"""
from __future__ import annotations

import pytest

import services.bot_pr_reaper as reaper
import services.finding_fix_applier as ffa
import services.fix_batches as fb
import services.loop_safety as ls

pytestmark = pytest.mark.asyncio


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs if docs is not None else []

    async def find_one(self, filt, proj=None):
        for d in self.docs:
            ok = True
            for k, v in filt.items():
                if isinstance(v, dict):
                    # Mirrors real Mongo's operator-interpretation —
                    # a dict value is NOT a plain equality match, so
                    # this filter key must not be skipped silently.
                    # The fake intentionally does NOT implement $ne/
                    # $gt/etc — it treats an unexpected operator dict
                    # as "matches anything", the worst case, so a test
                    # relying on real equality semantics here would be
                    # a false negative, not a false positive.
                    continue
                if d.get(k) != v:
                    ok = False
                    break
            if ok:
                return dict(d)
        return None

    async def insert_one(self, row):
        self.docs.append(dict(row))

    async def update_one(self, filt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items() if not isinstance(v, dict)):
                d.update(update.get("$set", {}))

    async def update_many(self, filt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items() if not isinstance(v, dict)):
                d.update(update.get("$set", {}))


class _FakeDB:
    def __init__(self):
        self.cto_projects = _FakeColl()
        self.finding_fixes = _FakeColl()
        self.fix_batches = _FakeColl()

    def __getitem__(self, name):
        return getattr(self, name)


# ───────────────────────────────── IDOR ────────────────────────────────────
class TestIDOR:
    async def test_get_batch_by_id_never_resolves_cross_user(self):
        db = _FakeDB()
        db.fix_batches.docs.append({
            "batch_id": "fb_victim", "user_id": "victim", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "pr_number": 1, "status": "open",
        })
        result = await fb.get_batch_by_id(
            db, batch_id="fb_victim", user_id="attacker", project_id="p1",
        )
        assert result is None

    async def test_get_batch_by_id_never_resolves_cross_project(self):
        db = _FakeDB()
        db.fix_batches.docs.append({
            "batch_id": "fb_victim", "user_id": "u1", "project_id": "p_victim",
            "branch": "aurem/fixes-aaaaaaaa", "pr_number": 1, "status": "open",
        })
        result = await fb.get_batch_by_id(
            db, batch_id="fb_victim", user_id="u1", project_id="p_attacker",
        )
        assert result is None

    async def test_revert_batch_cross_tenant_is_not_found_not_leaked(self):
        db = _FakeDB()
        db.fix_batches.docs.append({
            "batch_id": "fb_victim", "user_id": "victim", "project_id": "p1",
            "branch": "aurem/fixes-aaaaaaaa", "pr_number": 1, "status": "open",
        })
        result = await ffa.revert_batch(
            db, batch_id="fb_victim", user_id="attacker", project_id="p1", token="tok",
        )
        assert result == {"ok": False, "error": "batch_not_found"}


# ───────────────────────────── Mongo injection ──────────────────────────────
class TestMongoInjection:
    async def test_get_or_create_rejects_operator_dict_user_id(self):
        db = _FakeDB()
        result = await fb.get_or_create_open_batch(
            db, user_id={"$ne": None}, project_id="p1",
            owner="o", repo="r", base_branch="main", token="tok",
        )
        assert result == {"ok": False, "error": "invalid_user_id"}
        assert db.fix_batches.docs == []

    async def test_get_or_create_rejects_operator_dict_project_id(self):
        db = _FakeDB()
        result = await fb.get_or_create_open_batch(
            db, user_id="u1", project_id={"$gt": ""},
            owner="o", repo="r", base_branch="main", token="tok",
        )
        assert result == {"ok": False, "error": "invalid_project_id"}
        assert db.fix_batches.docs == []

    async def test_get_batch_by_id_rejects_non_string_ids(self):
        db = _FakeDB()
        db.fix_batches.docs.append({
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
        })
        assert await fb.get_batch_by_id(
            db, batch_id={"$ne": ""}, user_id="u1", project_id="p1",
        ) is None
        assert await fb.get_batch_by_id(
            db, batch_id="fb_1", user_id={"$ne": ""}, project_id="p1",
        ) is None


# ─────────────────────────── webhook forgery / replay ──────────────────────
class TestWebhookForgeryReplay:
    async def test_non_string_head_ref_never_reaches_mongo_filter_as_dict(self):
        """A crafted `head.ref` that deserializes to a dict (operator-
        injection shape) must be coerced to "" and refused, never
        used as the Mongo filter's value."""
        db = _FakeDB()
        db.fix_batches.docs.append({
            "batch_id": "fb_unrelated", "user_id": "someone_else",
            "project_id": "p_other", "branch": "aurem/fixes-bbbbbbbb",
            "pr_number": 2, "status": "open",
        })
        result = await ls.dispatch_pull_request_webhook(
            db, action="closed",
            payload={
                "pull_request": {
                    "merged": True,
                    "head": {"ref": {"$ne": "nonexistent"}},
                    "labels": [{"name": "aura:fix-batch"}],
                },
            },
        )
        # head_ref coerced to "" -> `is_batch_label and head_ref` is
        # False -> falls through to the "routed: none" default, and
        # critically the unrelated batch is UNTOUCHED.
        assert result.get("routed") != "fix_batch"
        assert db.fix_batches.docs[0]["status"] == "open"

    async def test_replaying_the_same_merge_event_is_idempotent(self):
        db = _FakeDB()
        db.fix_batches.docs.append({
            "batch_id": "fb_1", "user_id": "u1", "project_id": "p1",
            "branch": "aurem/fixes-cccccccc", "pr_number": 3, "status": "open",
        })
        db.finding_fixes.docs.append({"fix_id": "fx_1", "batch_id": "fb_1"})
        payload = {
            "pull_request": {
                "merged": True,
                "head": {"ref": "aurem/fixes-cccccccc"},
                "labels": [{"name": "aura:fix-batch"}],
            },
        }
        r1 = await ls.dispatch_pull_request_webhook(db, action="closed", payload=payload)
        r2 = await ls.dispatch_pull_request_webhook(db, action="closed", payload=payload)
        assert r1["status"] == "merged"
        assert r2["status"] == "merged"
        assert db.fix_batches.docs[0]["status"] == "merged"
        assert db.finding_fixes.docs[0]["merged"] is True
        # Replay must not raise, duplicate rows, or flip state back.

    async def test_webhook_with_no_matching_batch_does_not_raise(self):
        db = _FakeDB()
        result = await ls.dispatch_pull_request_webhook(
            db, action="closed",
            payload={
                "pull_request": {
                    "merged": True,
                    "head": {"ref": "aurem/fixes-ffffffff"},
                    "labels": [{"name": "aura:fix-batch"}],
                },
            },
        )
        assert result["routed"] == "fix_batch"
        assert result["batch_id"] is None


# ──────────────────────────── no secret/token leak ──────────────────────────
class TestNoSecretLeak:
    async def test_batch_unavailable_error_never_echoes_token(self, monkeypatch):
        async def _get_token(proj):
            return "ghp_SUPER_SECRET_TOKEN", None, None
        monkeypatch.setattr("services.pat_vault.get_repo_token_or_error", _get_token)

        async def _create_branch(**kw):
            return False, "base_ref_status_500"
        monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)

        db = _FakeDB()
        db.cto_projects.docs.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "o", "github_repo": "r", "github_branch": "main",
        })
        result = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u1"}, project_id="p1",
            finding={"rule_id": "r1", "file": "a.py", "line": 1},
        )
        assert result["ok"] is False
        assert "ghp_SUPER_SECRET_TOKEN" not in str(result)


# ───────────────────────────── bot_pr_reaper safety ─────────────────────────
class TestBotPrReaperSafety:
    async def test_batch_pr_body_excluded_from_bot_pr_listing(self, monkeypatch):
        batch_body = fb.build_pr_body([
            {"rule_id": "r1", "file": "a.py", "line": 1, "severity": "high",
             "commit_sha": "abc1234", "status": "fixed"},
        ])
        assert not any(m in batch_body for m in reaper.AUREM_BOT_MARKERS)

        legacy_body = "**AUREM auto-fix** for rule x"
        assert any(m in legacy_body for m in reaper.AUREM_BOT_MARKERS)

        class _Resp:
            status_code = 200
            def json(self):
                return [
                    {"number": 10, "body": legacy_body,
                     "head": {"ref": "aurem/fix-todo-1758100000"}},
                    {"number": 11, "body": batch_body,
                     "head": {"ref": "aurem/fixes-deadbeef"}},
                ]

        async def _fake_request(*a, **kw):
            return _Resp()
        monkeypatch.setattr(reaper, "github_request_with_retry", _fake_request)

        prs = await reaper._list_open_bot_prs(owner="o", repo="r", token="tok")
        numbers = {pr["number"] for pr in prs}
        assert numbers == {10}  # ONLY the legacy marker PR — batch PR excluded

    async def test_batch_pr_never_closed_or_grouped_by_reaper_sweep(self, monkeypatch):
        """Even if a batch PR somehow carried a trailing-timestamp-
        looking branch name, the reaper never sees it in the first
        place (previous test) — this confirms the full sweep closes
        NOTHING when the only two open bot PRs are a legacy singleton
        and a batch PR (never grouped together)."""
        batch_body = fb.build_pr_body([])
        legacy_body = "**AUREM auto-fix** for rule x"

        class _Resp:
            status_code = 200
            def json(self):
                return [
                    {"number": 20, "body": legacy_body,
                     "head": {"ref": "aurem/fix-only-one"}},
                    {"number": 21, "body": batch_body,
                     "head": {"ref": "aurem/fixes-feedface"}},
                ]

        async def _fake_request(*a, **kw):
            return _Resp()
        monkeypatch.setattr(reaper, "github_request_with_retry", _fake_request)

        closed_calls = []

        async def _fake_close(**kw):
            closed_calls.append(kw)
            return True, None
        monkeypatch.setattr(reaper, "close_pr", _fake_close)

        result = await reaper.reap_duplicate_bot_prs_for_repo(owner="o", repo="r", token="tok")
        assert result["ok"] is True
        assert result["open_bot_prs"] == 1  # batch PR invisible to the reaper
        assert closed_calls == []  # single PR per group -> nothing closed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
