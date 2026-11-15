"""tests/test_regression_iter2026_58_part2c_b2_webhook_wiring_both_doors.py

Part 2c B2 — services/loop_safety.py::dispatch_pull_request_webhook
must call record_version_on_merge for BOTH doors a merged AUREM
change can land through: a PR carrying label `aura:ship` (the Loop
"ship" flow) AND a PR carrying label `aura:fix-batch` (the shared
fix-batch flow). user_id/project_id come ONLY from our own stored
rows (loop_outcomes / fix_batches, looked up by head_ref) — never
from the webhook payload itself.

Mocks record_version_on_merge directly (its own correctness is
covered exhaustively by test_regression_iter2026_57) — this file
proves the WIRING: right call, right args, right door, every time,
and that a forged/malformed payload never reaches it with the wrong
identity.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _MemColl:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def find_one(self, filt=None, projection=None, sort=None):
        filt = filt or {}
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items() if not isinstance(v, dict)):
                return dict(r)
        return None

    async def update_one(self, filt, ops, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items() if not isinstance(v, dict)):
                if "$set" in ops:
                    r.update(ops["$set"])
                return
        if upsert:
            new = dict(filt)
            if "$set" in ops:
                new.update(ops["$set"])
            self.rows.append(new)

    async def update_many(self, filt, ops):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items() if not isinstance(v, dict)) and "$set" in ops:
                r.update(ops["$set"])

    async def insert_one(self, doc):
        self.rows.append(dict(doc))


class _MemDB:
    def __init__(self):
        self._colls = {}

    def __getattr__(self, name):
        if name not in self._colls:
            self._colls[name] = _MemColl()
        return self._colls[name]

    def __getitem__(self, name):
        return getattr(self, name)


MERGED_SHA = "f" * 40
SHIP_BRANCH = "auremcto/ship-l1"
BATCH_BRANCH = "aurem/fixes-aaaaaaaa"


def _ship_payload(*, merged=True, action="closed"):
    return {
        "action": action,
        "repository": {"full_name": "acme/widgets"},
        "pull_request": {
            "number": 7, "merged": merged,
            "html_url": "https://github.com/acme/widgets/pull/7",
            "merge_commit_sha": MERGED_SHA if merged else None,
            "head": {"ref": SHIP_BRANCH},
            "labels": [{"name": "aura:ship"}],
            "title": "Add dark mode toggle",
        },
    }


def _batch_payload(*, merged=True, action="closed"):
    return {
        "action": action,
        "repository": {"full_name": "acme/widgets"},
        "pull_request": {
            "number": 9, "merged": merged,
            "html_url": "https://github.com/acme/widgets/pull/9",
            "merge_commit_sha": MERGED_SHA if merged else None,
            "head": {"ref": BATCH_BRANCH},
            "labels": [{"name": "aura:fix-batch"}],
            "title": "AUREM fixes — 3 issues",
        },
    }


class TestShipDoorRecordsVersion:
    async def test_merged_ship_pr_records_a_version_with_the_right_identity(self, monkeypatch):
        from services import loop_safety
        db = _MemDB()
        db.loop_outcomes.rows.append({
            "loop_id": "l1", "user_id": "u1", "project_id": "p1",
            "commit_sha": "a" * 40, "ship_branch": SHIP_BRANCH, "pr_status": "open",
        })

        calls = []

        async def _fake_record(db_, **kw):
            calls.append(kw)
        monkeypatch.setattr(loop_safety, "record_version_on_merge", _fake_record)

        await loop_safety.dispatch_pull_request_webhook(
            db, payload=_ship_payload(merged=True), action="closed",
        )
        assert len(calls) == 1
        call = calls[0]
        assert call["user_id"] == "u1"
        assert call["project_id"] == "p1"
        assert call["merged_sha"] == MERGED_SHA
        assert call["source"] == "ship_pr"
        assert call["branch"] == SHIP_BRANCH
        assert call["loop_id"] == "l1"
        assert call["pr_url"] == "https://github.com/acme/widgets/pull/7"

    async def test_unmerged_ship_pr_never_records_a_version(self, monkeypatch):
        from services import loop_safety
        db = _MemDB()
        db.loop_outcomes.rows.append({
            "loop_id": "l1", "user_id": "u1", "project_id": "p1",
            "ship_branch": SHIP_BRANCH, "pr_status": "open",
        })
        calls = []

        async def _fake_record(db_, **kw):
            calls.append(kw)
        monkeypatch.setattr(loop_safety, "record_version_on_merge", _fake_record)

        await loop_safety.dispatch_pull_request_webhook(
            db, payload=_ship_payload(merged=False), action="closed",
        )
        assert calls == []

    async def test_identity_comes_from_loop_outcomes_never_from_the_payload(self, monkeypatch):
        """Even if an attacker-controlled webhook payload somehow
        carried user_id/project_id fields, they must never be used —
        only the SERVER'S OWN loop_outcomes row (matched by
        ship_branch) is trusted."""
        from services import loop_safety
        db = _MemDB()
        db.loop_outcomes.rows.append({
            "loop_id": "l1", "user_id": "REAL_OWNER", "project_id": "REAL_PROJECT",
            "ship_branch": SHIP_BRANCH, "pr_status": "open",
        })
        payload = _ship_payload(merged=True)
        payload["user_id"] = "attacker_controlled_user"
        payload["project_id"] = "attacker_controlled_project"

        calls = []

        async def _fake_record(db_, **kw):
            calls.append(kw)
        monkeypatch.setattr(loop_safety, "record_version_on_merge", _fake_record)

        await loop_safety.dispatch_pull_request_webhook(db, payload=payload, action="closed")
        assert calls[0]["user_id"] == "REAL_OWNER"
        assert calls[0]["project_id"] == "REAL_PROJECT"

    async def test_no_matching_loop_outcomes_row_never_records_a_version(self, monkeypatch):
        """A ship-labeled PR with no matching loop_outcomes row (bogus
        branch, already-cleaned-up row) must not fabricate a version
        from payload data alone."""
        from services import loop_safety
        db = _MemDB()  # no loop_outcomes row at all

        calls = []

        async def _fake_record(db_, **kw):
            calls.append(kw)
        monkeypatch.setattr(loop_safety, "record_version_on_merge", _fake_record)

        await loop_safety.dispatch_pull_request_webhook(
            db, payload=_ship_payload(merged=True), action="closed",
        )
        assert calls == []


class TestFixBatchDoorRecordsVersion:
    async def test_merged_fix_batch_pr_records_a_version_with_the_right_identity(self, monkeypatch):
        from services import loop_safety
        db = _MemDB()
        db.fix_batches.rows.append({
            "batch_id": "fb_1", "user_id": "u2", "project_id": "p2",
            "branch": BATCH_BRANCH, "status": "open",
        })

        calls = []

        async def _fake_record(db_, **kw):
            calls.append(kw)
        monkeypatch.setattr(loop_safety, "record_version_on_merge", _fake_record)

        await loop_safety.dispatch_pull_request_webhook(
            db, payload=_batch_payload(merged=True), action="closed",
        )
        assert len(calls) == 1
        call = calls[0]
        assert call["user_id"] == "u2"
        assert call["project_id"] == "p2"
        assert call["merged_sha"] == MERGED_SHA
        assert call["source"] == "fix_batch"
        assert call["batch_id"] == "fb_1"

    async def test_closed_not_merged_fix_batch_pr_never_records_a_version(self, monkeypatch):
        from services import loop_safety
        db = _MemDB()
        db.fix_batches.rows.append({
            "batch_id": "fb_1", "user_id": "u2", "project_id": "p2",
            "branch": BATCH_BRANCH, "status": "open",
        })
        calls = []

        async def _fake_record(db_, **kw):
            calls.append(kw)
        monkeypatch.setattr(loop_safety, "record_version_on_merge", _fake_record)

        await loop_safety.dispatch_pull_request_webhook(
            db, payload=_batch_payload(merged=False), action="closed",
        )
        assert calls == []

    async def test_no_matching_fix_batches_row_never_records_a_version(self, monkeypatch):
        from services import loop_safety
        db = _MemDB()  # no fix_batches row

        calls = []

        async def _fake_record(db_, **kw):
            calls.append(kw)
        monkeypatch.setattr(loop_safety, "record_version_on_merge", _fake_record)

        await loop_safety.dispatch_pull_request_webhook(
            db, payload=_batch_payload(merged=True), action="closed",
        )
        assert calls == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
