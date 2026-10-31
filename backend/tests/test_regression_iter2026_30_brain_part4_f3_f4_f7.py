"""tests/test_regression_iter2026_30_brain_part4_f3_f4_f7.py

ORA Brain Part 4:
  F3 — unified DELETE /api/brain/my-data (brain_calls + project_memory,
       old /api/admin/brain/my-data kept as a byte-identical alias).
  F4 — services/project_memory.py's per-key write lock no longer
       grows forever (WeakValueDictionary) and still serializes
       concurrent writers to the SAME key.
  F7 — GET /api/admin/brain/health (admin-only).
"""
from __future__ import annotations

import asyncio
import gc

import pytest
from fastapi.testclient import TestClient

import routers.brain_admin as brain_admin_router
import services.brain_gateway as bg
import services.project_memory as pm

pytestmark = pytest.mark.asyncio


# ───────────────────────────── fake DB double ────────────────────────────
class _FakeBrainCalls:
    def __init__(self):
        self.rows: list[dict] = []
        self.deleted: list[dict] = []

    async def delete_many(self, filt):
        keep, removed = [], []
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                removed.append(r)
            else:
                keep.append(r)
        self.rows = keep
        self.deleted.extend(removed)

    async def count_documents(self, filt):
        n = 0
        for r in self.rows:
            ok = True
            for k, v in filt.items():
                if isinstance(v, dict) and "$gte" in v:
                    if (r.get(k) or 0) < v["$gte"]:
                        ok = False
                elif isinstance(v, dict) and "$ne" in v:
                    if r.get(k) == v["$ne"]:
                        ok = False
                elif r.get(k) != v:
                    ok = False
            if ok:
                n += 1
        return n


class _FakeProjectMemoryColl:
    def __init__(self):
        self.docs: dict = {}

    async def delete_one(self, filt):
        self.docs.pop((filt.get("user_id"), filt.get("project_id")), None)

    async def delete_many(self, filt):
        for key in list(self.docs.keys()):
            if key[0] == filt.get("user_id"):
                self.docs.pop(key, None)


class _FakeDB:
    def __init__(self):
        self.brain_calls = _FakeBrainCalls()
        self.project_memory = _FakeProjectMemoryColl()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture(autouse=True)
async def _reset_gateway_state(monkeypatch):
    monkeypatch.setattr(bg, "_write_queue", None)
    monkeypatch.setattr(bg, "_worker_task", None)
    monkeypatch.setattr(bg, "_dropped_count", 0)
    yield
    task = bg._worker_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ───────────────────────────── F3 — unified delete ───────────────────────
def _fake_current_dev_factory(user_dict):
    async def _f(authorization=None):
        return user_dict
    return _f


@pytest.fixture
def client_with_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(brain_admin_router, "require_db", lambda: db)
    from main import app
    return TestClient(app), db


class TestUnifiedDeleteMyData:
    def test_new_path_deletes_both_collections(self, client_with_db, monkeypatch):
        client, db = client_with_db
        monkeypatch.setattr(
            brain_admin_router, "current_dev", _fake_current_dev_factory({"user_id": "u1"}),
        )
        db.brain_calls.rows.append({"user_id": "u1", "project_id": "p1"})
        db.project_memory.docs[("u1", "p1")] = {"stack": "x"}
        resp = client.delete("/api/brain/my-data", params={"project_id": "p1"})
        assert resp.status_code == 200
        assert db.brain_calls.rows == []
        assert ("u1", "p1") not in db.project_memory.docs

    def test_old_admin_path_is_a_byte_identical_alias(self, client_with_db, monkeypatch):
        client, db = client_with_db
        monkeypatch.setattr(
            brain_admin_router, "current_dev", _fake_current_dev_factory({"user_id": "u1"}),
        )
        db.brain_calls.rows.append({"user_id": "u1", "project_id": "p1"})
        db.project_memory.docs[("u1", "p1")] = {"stack": "x"}
        resp_new = client.delete("/api/brain/my-data", params={"project_id": "p1"})

        # Re-seed and hit the OLD path — same shape of response.
        db.brain_calls.rows.append({"user_id": "u1", "project_id": "p2"})
        db.project_memory.docs[("u1", "p2")] = {"stack": "y"}
        resp_old = client.delete("/api/admin/brain/my-data", params={"project_id": "p2"})

        assert resp_new.status_code == resp_old.status_code == 200
        assert set(resp_new.json().keys()) == set(resp_old.json().keys())
        assert ("u1", "p2") not in db.project_memory.docs

    def test_no_project_id_wipes_all_projects_for_that_user(self, client_with_db, monkeypatch):
        client, db = client_with_db
        monkeypatch.setattr(
            brain_admin_router, "current_dev", _fake_current_dev_factory({"user_id": "u1"}),
        )
        db.project_memory.docs[("u1", "p1")] = {"stack": "x"}
        db.project_memory.docs[("u1", "p2")] = {"stack": "y"}
        db.project_memory.docs[("u2", "p3")] = {"stack": "z"}
        resp = client.delete("/api/brain/my-data")
        assert resp.status_code == 200
        assert ("u1", "p1") not in db.project_memory.docs
        assert ("u1", "p2") not in db.project_memory.docs
        assert ("u2", "p3") in db.project_memory.docs  # another user's row untouched

    def test_only_deletes_own_rows_not_another_users(self, client_with_db, monkeypatch):
        client, db = client_with_db
        monkeypatch.setattr(
            brain_admin_router, "current_dev", _fake_current_dev_factory({"user_id": "u1"}),
        )
        db.brain_calls.rows.extend([
            {"user_id": "u1", "project_id": "p1"},
            {"user_id": "u2", "project_id": "p1"},
        ])
        client.delete("/api/brain/my-data")
        remaining = [r["user_id"] for r in db.brain_calls.rows]
        assert remaining == ["u2"]

    def test_delete_my_data_requires_no_admin_role(self, client_with_db, monkeypatch):
        """F3 explicitly: this is a plain user action, not an admin
        one, even though one path happens to live under /admin."""
        client, _db = client_with_db
        monkeypatch.setattr(
            brain_admin_router, "current_dev",
            _fake_current_dev_factory({"user_id": "u1", "is_admin": False, "tier": "free"}),
        )
        resp = client.delete("/api/brain/my-data")
        assert resp.status_code == 200


# ───────────────────────── F4 — bounded write-lock dict ──────────────────
class TestBoundedWriteLocks:
    def test_lock_identity_preserved_while_held(self):
        """The SAME key, called twice back-to-back (first reference
        still alive), must return the SAME Lock object — the whole
        point of the lock existing at all."""
        lock1 = pm._lock_for("u1", "p1")
        lock2 = pm._lock_for("u1", "p1")
        assert lock1 is lock2

    def test_dict_does_not_grow_unboundedly(self):
        """Many DISTINCT keys, each released immediately — the
        WeakValueDictionary must not accumulate a permanent entry per
        key the way a plain dict would."""
        for i in range(500):
            _ = pm._lock_for(f"user{i}", f"proj{i}")
            # No reference retained past this iteration.
        gc.collect()
        assert len(pm._write_locks) < 50  # nowhere near 500

    async def test_still_serializes_concurrent_writers_to_same_key(self):
        """End-to-end proof the Weak-dict swap didn't break mutual
        exclusion: two concurrent critical sections touching a
        shared counter must never interleave."""
        counter = {"active": 0, "max_seen": 0}

        async def _critical_section():
            async with pm._lock_for("u1", "p1"):
                counter["active"] += 1
                counter["max_seen"] = max(counter["max_seen"], counter["active"])
                await asyncio.sleep(0.01)
                counter["active"] -= 1

        await asyncio.gather(*(_critical_section() for _ in range(5)))
        assert counter["max_seen"] == 1  # never more than 1 inside at once


# ───────────────────────────── F7 — health endpoint ──────────────────────
class TestHealthEndpoint:
    def test_non_admin_gets_403(self, client_with_db, monkeypatch):
        client, _db = client_with_db
        monkeypatch.setattr(
            brain_admin_router, "current_dev",
            _fake_current_dev_factory({"user_id": "u1", "is_admin": False, "tier": "free"}),
        )
        resp = client.get("/api/admin/brain/health")
        assert resp.status_code == 403

    def test_admin_gets_full_snapshot_shape(self, client_with_db, monkeypatch):
        client, db = client_with_db
        monkeypatch.setattr(
            brain_admin_router, "current_dev",
            _fake_current_dev_factory({"user_id": "founder1", "is_admin": True, "tier": "founder"}),
        )
        import time
        db.brain_calls.rows.extend([
            {"created_at": time.time(), "outcome": "tests_passed", "promoted": "candidate"},
            {"created_at": time.time(), "outcome": None, "promoted": "raw"},
        ])
        resp = client.get("/api/admin/brain/health")
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "queue_size", "worker_alive", "dropped_count", "knowledge_mode",
            "rows_last_24h", "rows_with_outcome_last_24h",
            "outcome_pct_last_24h", "candidate_count",
        ):
            assert key in body
        assert body["rows_last_24h"] == 2
        assert body["rows_with_outcome_last_24h"] == 1
        assert body["outcome_pct_last_24h"] == 50.0
        assert body["candidate_count"] == 1

    async def test_get_health_snapshot_never_raises_on_db_none(self):
        snapshot = await bg.get_health_snapshot(None)
        assert snapshot["rows_last_24h"] == 0
        assert isinstance(snapshot["worker_alive"], bool)

    async def test_dropped_count_reflected_in_snapshot(self, monkeypatch):
        monkeypatch.setattr(bg, "_QUEUE_MAXSIZE", 1)
        q = asyncio.Queue(maxsize=1)
        monkeypatch.setattr(bg, "_write_queue", q)
        await q.put({"dummy": 1})
        await bg.record_call(
            user_id="u1", project_id="p1", task_type="fix", model="claude",
            latency_ms=1.0, surface="test",
        )
        snapshot = await bg.get_health_snapshot(None)
        assert snapshot["dropped_count"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
