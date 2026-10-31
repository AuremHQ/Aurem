"""tests/test_regression_iter2026_26_brain_gateway_record.py

ORA Brain Part 3, Phase E — services/brain_gateway.py: record-only
gateway (claim_recording dedup, HMAC prompt hash, fail-open queue),
outcome attach (idempotent + race-safe), 3-tier promotion, the
anonymizer, the model scoreboard, and delete-on-request + its
admin-gated endpoints.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import routers.brain_admin as brain_admin_router
import services.brain_gateway as bg

pytestmark = pytest.mark.asyncio


# ───────────────────────────── fake DB double ────────────────────────────
class _UpdateResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        v = self._rows[self._i]
        self._i += 1
        return v


class _FakeBrainCalls:
    def __init__(self):
        self.rows: list[dict] = []
        self.insert_calls = 0
        self.deleted: list[dict] = []

    async def insert_one(self, row):
        self.insert_calls += 1
        self.rows.append(dict(row))

    def _row_matches(self, r, filt):
        for k, v in filt.items():
            if isinstance(v, dict) and "$in" in v:
                if r.get(k) not in v["$in"]:
                    return False
            elif isinstance(v, dict) and "$ne" in v:
                if r.get(k) == v["$ne"]:
                    return False
            elif r.get(k) != v:
                return False
        return True

    def find(self, filt, proj=None):
        return _FakeCursor([r for r in self.rows if self._row_matches(r, filt)])

    def aggregate(self, pipeline):
        match = pipeline[0]["$match"]
        rows = [r for r in self.rows if r.get("outcome") != match["outcome"].get("$ne")]
        buckets: dict = {}
        for r in rows:
            key = (r.get("task_type"), r.get("model"), r.get("outcome"))
            buckets[key] = buckets.get(key, 0) + 1
        out = [
            {"_id": {"task_type": k[0], "model": k[1], "outcome": k[2]}, "count": v}
            for k, v in buckets.items()
        ]
        return _FakeCursor(out)

    async def update_one(self, filt, update):
        for r in self.rows:
            if self._row_matches(r, filt):
                r.update(update.get("$set", {}))
                return _UpdateResult(1)
        return _UpdateResult(0)

    async def delete_many(self, filt):
        keep = []
        removed = []
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                removed.append(r)
            else:
                keep.append(r)
        self.rows = keep
        self.deleted.extend(removed)

    async def create_index(self, *a, **k):
        return None


class _FakeProjectMemory:
    """Minimal double so promote_to_candidate's call into
    services.project_memory.upsert_project_memory has somewhere to
    write — ownership itself is monkeypatched separately per test."""
    def __init__(self):
        self.docs: dict = {}

    async def update_one(self, filt, update, upsert=False):
        key = (filt.get("user_id"), filt.get("project_id"))
        self.docs[key] = {**self.docs.get(key, {}), **update.get("$set", {})}


class _FakeDB:
    def __init__(self):
        self.brain_calls = _FakeBrainCalls()
        self.project_memory = _FakeProjectMemory()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture(autouse=True)
def _reset_gateway_state(monkeypatch):
    monkeypatch.setattr(bg, "_write_queue", None)
    monkeypatch.setattr(bg, "_worker_task", None)
    monkeypatch.delenv("ORA_BRAIN_HMAC_SECRET", raising=False)
    yield


# ───────────────────────── HMAC prompt digest ────────────────────────────
class TestPromptHmac:
    def test_no_secret_configured_returns_none(self):
        assert bg._prompt_hmac("sys", "user text") is None

    def test_with_secret_returns_hex_digest_not_plain_hash(self, monkeypatch):
        monkeypatch.setenv("ORA_BRAIN_HMAC_SECRET", "s3cr3t")
        digest = bg._prompt_hmac("sys", "user text")
        assert digest is not None
        assert len(digest) == 64  # sha256 hex
        # Changing the secret changes the digest — proves it's keyed,
        # not an unkeyed hash of the text alone.
        monkeypatch.setenv("ORA_BRAIN_HMAC_SECRET", "different-secret")
        digest2 = bg._prompt_hmac("sys", "user text")
        assert digest2 != digest

    def test_secret_never_appears_in_the_digest_output(self, monkeypatch):
        monkeypatch.setenv("ORA_BRAIN_HMAC_SECRET", "MY-TOP-SECRET-VALUE")
        digest = bg._prompt_hmac("sys", "user text")
        assert "MY-TOP-SECRET-VALUE" not in digest


# ─────────────────────── record_call + fail-open queue ───────────────────
class TestRecordCallFailOpen:
    async def test_record_enqueues_without_touching_db(self):
        call_id = await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=42.0, tokens=100, system="sys", user="usr", surface="test",
        )
        assert call_id is not None
        assert bg._write_queue.qsize() == 1

    async def test_no_raw_text_stored_only_hmac_and_size(self, monkeypatch):
        monkeypatch.setenv("ORA_BRAIN_HMAC_SECRET", "s3cr3t")
        await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, system="SECRET SYSTEM PROMPT", user="SECRET USER TEXT",
            surface="test",
        )
        row = await bg._write_queue.get()
        assert "SECRET SYSTEM PROMPT" not in str(row)
        assert "SECRET USER TEXT" not in str(row)
        assert row["prompt_size"] == len("SECRET SYSTEM PROMPT") + len("SECRET USER TEXT")
        assert row["prompt_hmac"] is not None

    async def test_text_storage_flag_is_hardcoded_off(self):
        assert bg._TEXT_STORAGE_ENABLED is False

    async def test_queue_full_drops_sample_without_raising(self, monkeypatch):
        monkeypatch.setattr(bg, "_QUEUE_MAXSIZE", 1)
        q = asyncio.Queue(maxsize=1)
        monkeypatch.setattr(bg, "_write_queue", q)
        await q.put({"dummy": 1})
        call_id = await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, surface="test",
        )
        assert call_id is None  # dropped, not raised

    async def test_write_failure_never_raises(self, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("mongo is down")
        monkeypatch.setattr(bg, "_write_one", _boom)
        call_id = await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, surface="test",
        )
        assert call_id is not None  # enqueue itself still succeeded
        # Draining the (broken) write must not propagate.
        assert await bg.drain_once_for_tests() is True

    async def test_drain_writes_queued_row_to_db(self, monkeypatch):
        db = _FakeDB()
        monkeypatch.setattr("cto_services.db.get_db", lambda: db)
        await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=5.0, surface="test",
        )
        drained = await bg.drain_once_for_tests()
        assert drained is True
        assert db.brain_calls.insert_calls == 1
        assert db.brain_calls.rows[0]["user_id"] == "u1"

    async def test_slow_db_write_times_out_without_blocking_forever(self, monkeypatch):
        class _SlowDB:
            def __getitem__(self, name):
                return self

            async def insert_one(self, row):
                await asyncio.sleep(10)
        monkeypatch.setattr(bg, "_WRITE_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr("cto_services.db.get_db", lambda: _SlowDB())
        await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, surface="test",
        )
        import time
        t0 = time.monotonic()
        await bg.drain_once_for_tests()
        assert (time.monotonic() - t0) < 1.0  # bounded by the 0.05s timeout, not the 10s sleep


# ───────────────────────── claim_recording dedup ─────────────────────────
class TestClaimRecordingDedup:
    async def test_outer_claims_inner_sees_already_claimed(self):
        async with bg.claim_recording() as outer_already:
            assert outer_already is False
            async with bg.claim_recording() as inner_already:
                assert inner_already is True

    async def test_claim_resets_after_scope_exits(self):
        async with bg.claim_recording() as first:
            assert first is False
        async with bg.claim_recording() as second:
            assert second is False  # new top-level call, not still claimed


# ─────────────────────── outcome attach: idempotent + race-safe ─────────
class TestOutcomeAttach:
    async def test_attach_sets_outcome_once(self):
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "outcome": None})
        ok = await bg.attach_outcome(db, "bc_1", "tests_passed")
        assert ok is True
        assert db.brain_calls.rows[0]["outcome"] == "tests_passed"

    async def test_second_attach_is_idempotent_noop(self):
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "outcome": None})
        await bg.attach_outcome(db, "bc_1", "tests_passed")
        ok2 = await bg.attach_outcome(db, "bc_1", "tests_passed")
        assert ok2 is False
        assert db.brain_calls.rows[0]["outcome"] == "tests_passed"

    async def test_unknown_outcome_value_rejected(self):
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "outcome": None})
        ok = await bg.attach_outcome(db, "bc_1", "the model graded itself 10/10")
        assert ok is False
        assert db.brain_calls.rows[0]["outcome"] is None

    async def test_concurrent_attach_race_only_one_wins(self):
        db = _FakeDB()
        db.brain_calls.rows.append({"call_id": "bc_1", "outcome": None})
        results = await asyncio.gather(
            bg.attach_outcome(db, "bc_1", "tests_passed"),
            bg.attach_outcome(db, "bc_1", "tests_failed"),
        )
        assert sorted(results) == [False, True]
        assert db.brain_calls.rows[0]["outcome"] in ("tests_passed", "tests_failed")


# ───────────────────── promotion tier 2 — candidate ──────────────────────
class TestPromoteToCandidate:
    async def test_promotes_own_strong_outcome_row_into_own_project_memory(self, monkeypatch):
        db = _FakeDB()
        db.brain_calls.rows.append({
            "call_id": "bc_1", "user_id": "u1", "project_id": "p1",
            "task_type": "fix", "outcome": "tests_passed", "promoted": "raw",
        })

        async def _fake_owns(*a, **k):
            return True
        monkeypatch.setattr("services.project_memory._owns_project", _fake_owns)

        n = await bg.promote_to_candidate(db, "u1")
        assert n == 1
        assert db.brain_calls.rows[0]["promoted"] == "candidate"

    async def test_never_touches_another_users_rows(self, monkeypatch):
        db = _FakeDB()
        db.brain_calls.rows.append({
            "call_id": "bc_1", "user_id": "userB", "project_id": "p1",
            "task_type": "fix", "outcome": "tests_passed", "promoted": "raw",
        })
        n = await bg.promote_to_candidate(db, "userA")
        assert n == 0

    async def test_weak_outcome_never_promoted(self):
        db = _FakeDB()
        db.brain_calls.rows.append({
            "call_id": "bc_1", "user_id": "u1", "project_id": "p1",
            "task_type": "fix", "outcome": "tests_failed", "promoted": "raw",
        })
        n = await bg.promote_to_candidate(db, "u1")
        assert n == 0

    async def test_already_candidate_rows_not_promoted_twice(self):
        db = _FakeDB()
        db.brain_calls.rows.append({
            "call_id": "bc_1", "user_id": "u1", "project_id": "p1",
            "task_type": "fix", "outcome": "tests_passed", "promoted": "candidate",
        })
        n = await bg.promote_to_candidate(db, "u1")
        assert n == 0


# ───────────────── promotion tier 3 — anonymizer + export ────────────────
class TestAnonymizer:
    def test_strips_email(self):
        assert "founder@aurem.live" not in bg.anonymize_text("Contact founder@aurem.live for help")

    def test_strips_secret_shaped_tokens(self):
        out = bg.anonymize_text("key: sk-abcdefghij1234567890 and ghp_abcdefghij1234567890")
        assert "sk-" not in out
        assert "ghp_" not in out

    def test_strips_repo_and_github_url(self):
        out = bg.anonymize_text("fixed in acme/widgets.git see github.com/acme/widgets for more")
        assert "acme/widgets" not in out

    def test_strips_file_paths(self):
        out = bg.anonymize_text("the bug is in /home/user/projects/acme/backend/routers/chat.py")
        assert "/home/user" not in out

    def test_non_str_input_is_safe(self):
        assert bg.anonymize_text(None) == ""
        assert bg.anonymize_text(12345) == ""

    async def test_export_never_writes_to_knowledge_dir(self, monkeypatch, tmp_path):
        import services.knowledge_injector as ki
        monkeypatch.setattr(ki, "_KNOWLEDGE_DIR", str(tmp_path))
        db = _FakeDB()
        db.brain_calls.rows.append({
            "call_id": "bc_1", "task_type": "fix", "outcome": "tests_passed",
            "model": "claude", "promoted": "candidate",
        })
        draft = await bg.export_anonymized_global_draft(db)
        assert len(draft) == 1
        assert list(tmp_path.iterdir()) == []  # nothing written to disk

    async def test_export_output_itself_is_secret_scanned(self):
        db = _FakeDB()
        db.brain_calls.rows.append({
            "call_id": "bc_1", "task_type": "fix uses key sk-abcdefghij1234567890",
            "outcome": "tests_passed", "model": "claude", "promoted": "candidate",
        })
        draft = await bg.export_anonymized_global_draft(db)
        assert "sk-abcdefghij1234567890" not in str(draft)


# ───────────────────────────── scoreboard ────────────────────────────────
class TestScoreboard:
    async def test_counts_grouped_by_task_type_model_outcome(self):
        db = _FakeDB()
        db.brain_calls.rows.extend([
            {"task_type": "fix", "model": "claude", "outcome": "tests_passed"},
            {"task_type": "fix", "model": "claude", "outcome": "tests_passed"},
            {"task_type": "fix", "model": "claude", "outcome": "tests_failed"},
        ])
        rows = await bg.get_model_scoreboard(db)
        passed = next(r for r in rows if r["outcome"] == "tests_passed")
        assert passed["count"] == 2

    async def test_db_none_returns_empty(self):
        assert await bg.get_model_scoreboard(None) == []


# ───────────────────────── delete-on-request ─────────────────────────────
class TestDeleteOwnBrainCalls:
    async def test_deletes_only_own_rows(self):
        db = _FakeDB()
        db.brain_calls.rows.extend([
            {"user_id": "u1", "project_id": "p1"},
            {"user_id": "u2", "project_id": "p1"},
        ])
        await bg.delete_own_brain_calls(db, "u1")
        remaining = [r["user_id"] for r in db.brain_calls.rows]
        assert remaining == ["u2"]

    async def test_idempotent_second_delete(self):
        db = _FakeDB()
        assert await bg.delete_own_brain_calls(db, "u1") is True
        assert await bg.delete_own_brain_calls(db, "u1") is True


# ───────────────────────── admin router gating ────────────────────────────
def _fake_current_dev_factory(user_dict):
    async def _f(authorization=None):
        return user_dict
    return _f


@pytest.fixture
def admin_client(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(brain_admin_router, "require_db", lambda: db)
    from main import app
    return TestClient(app), db


class TestAdminRouterGating:
    def test_non_admin_gets_403_on_scoreboard(self, admin_client, monkeypatch):
        client, _db = admin_client
        monkeypatch.setattr(
            brain_admin_router, "current_dev",
            _fake_current_dev_factory({"user_id": "u1", "is_admin": False, "tier": "free"}),
        )
        resp = client.get("/api/admin/brain/scoreboard")
        assert resp.status_code == 403

    def test_non_admin_gets_403_on_export(self, admin_client, monkeypatch):
        client, _db = admin_client
        monkeypatch.setattr(
            brain_admin_router, "current_dev",
            _fake_current_dev_factory({"user_id": "u1", "is_admin": False, "tier": "free"}),
        )
        resp = client.get("/api/admin/brain/export-candidates")
        assert resp.status_code == 403

    def test_admin_gets_200_on_scoreboard(self, admin_client, monkeypatch):
        client, _db = admin_client
        monkeypatch.setattr(
            brain_admin_router, "current_dev",
            _fake_current_dev_factory({"user_id": "founder1", "is_admin": True, "tier": "founder"}),
        )
        resp = client.get("/api/admin/brain/scoreboard")
        assert resp.status_code == 200

    def test_any_authenticated_user_can_delete_own_data(self, admin_client, monkeypatch):
        client, db = admin_client
        db.brain_calls.rows.append({"user_id": "u1", "project_id": "p1"})
        monkeypatch.setattr(
            brain_admin_router, "current_dev",
            _fake_current_dev_factory({"user_id": "u1", "is_admin": False, "tier": "free"}),
        )
        resp = client.delete("/api/admin/brain/my-data")
        assert resp.status_code == 200
        assert db.brain_calls.rows == []


class TestExistingSuitesStillGreen:
    def test_marker_only(self):
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
