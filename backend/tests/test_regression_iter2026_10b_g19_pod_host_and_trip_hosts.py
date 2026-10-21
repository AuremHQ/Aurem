"""tests/test_regression_iter2026_10b_g19_pod_host_and_trip_hosts.py

G19 follow-up fixes (2026-10, round 2):

1. `_pod_host()` strip-order bug — `POD_NAME=" "` (whitespace-only) was
   truthy BEFORE stripping, so it suppressed a real `HOSTNAME` and only
   THEN got stripped to "" -> "unknown", silently disabling the whole
   per-pod G19 scoping fix. Each candidate is now stripped first, then
   the first non-empty one wins; falling back to "unknown" logs once.

2. `_shutdown_marker_id(host)` — single source of truth for the
   per-host shutdown-marker `_id`, used by both the read (classify)
   and write (mark_clean_shutdown) sides.

3. `_trip_loop` now records a per-host breakdown (`hosts`,
   `host_counts`) on the `process_loop_trips` row, so a postmortem can
   tell "one pod loop-crashing 3x" apart from "3 different pods each
   crashed once".
"""
import logging

import pytest

from services import process_recovery as pr


class _FakeCollection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def count_documents(self, query):
        return sum(1 for d in self.docs if self._match(d, query))

    async def find_one(self, query, proj=None, sort=None):
        rows = [d for d in self.docs if self._match(d, query)]
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda d: d.get(key, 0), reverse=direction < 0)
        return rows[0] if rows else None

    def find(self, query, proj=None):
        rows = [d for d in self.docs if self._match(d, query)]
        return _FakeCursor(rows)

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if self._match(d, query):
                self._apply(d, update)
                return
        if upsert:
            d = {k: v for k, v in query.items() if not isinstance(v, dict)}
            self._apply(d, update)
            self.docs.append(d)

    @staticmethod
    def _apply(d, update):
        for k, v in update.get("$set", {}).items():
            d[k] = v

    @staticmethod
    def _match(d, query):
        for k, cond in query.items():
            v = d.get(k)
            if isinstance(cond, dict):
                if "$gte" in cond and not (v is not None and v >= cond["$gte"]):
                    return False
            elif v != cond:
                return False
        return True


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._i]
        self._i += 1
        return row


class _FakeDB:
    def __init__(self):
        self.process_boots = _FakeCollection()
        self.process_loop_trips = _FakeCollection()
        self.topup_alerts = _FakeCollection()
        self.process_shutdown_marker = _FakeCollection()


class TestPodHostStripOrder:
    def test_whitespace_only_pod_name_falls_through_to_hostname(self, monkeypatch):
        monkeypatch.setenv("POD_NAME", " ")
        monkeypatch.setenv("HOSTNAME", "real-pod-xyz")
        assert pr._pod_host() == "real-pod-xyz"

    def test_both_unset_falls_back_to_unknown_and_warns_once(self, monkeypatch, caplog):
        monkeypatch.delenv("POD_NAME", raising=False)
        monkeypatch.delenv("HOSTNAME", raising=False)
        monkeypatch.setattr(pr, "_UNKNOWN_HOST_WARNED", False)
        with caplog.at_level(logging.WARNING, logger="services.process_recovery"):
            assert pr._pod_host() == "unknown"
            assert pr._pod_host() == "unknown"
        warnings = [r for r in caplog.records if "per-pod scoping inactive" in r.message]
        assert len(warnings) == 1  # only warned once across both calls


class TestShutdownMarkerIdHelper:
    def test_classify_and_mark_use_the_same_marker_id(self, monkeypatch):
        monkeypatch.setenv("HOSTNAME", "pod-shared-id")
        assert pr._shutdown_marker_id("pod-shared-id") == "last_clean_shutdown::pod-shared-id"

    @pytest.mark.asyncio
    async def test_mark_clean_shutdown_writes_id_classify_can_read(self, monkeypatch):
        monkeypatch.setenv("HOSTNAME", "pod-a")
        db = _FakeDB()
        await pr.record_boot(db, reason="supervisor_start")
        await pr.mark_clean_shutdown(db)
        marker = await db.process_shutdown_marker.find_one(
            {"_id": pr._shutdown_marker_id("pod-a")})
        assert marker is not None
        await pr.record_boot(db, reason="supervisor_start")
        assert db.process_boots.docs[-1]["reason"] == "clean_restart"


class TestTripLoopHostBreakdown:
    @pytest.mark.asyncio
    async def test_three_hosts_one_crash_each(self, monkeypatch):
        monkeypatch.setattr(pr, "LOOP_THRESHOLD", 3)
        monkeypatch.setattr(pr, "LOOP_WINDOW_S", 600)
        db = _FakeDB()
        for pod in ("pod-a", "pod-b", "pod-c"):
            monkeypatch.setenv("HOSTNAME", pod)
            await pr.record_boot(db, reason="crash_or_kill")
        trip = db.process_loop_trips.docs[0]
        assert sorted(trip["hosts"]) == ["pod-a", "pod-b", "pod-c"]
        assert trip["host_counts"] == {"pod-a": 1, "pod-b": 1, "pod-c": 1}

    @pytest.mark.asyncio
    async def test_one_host_three_crashes(self, monkeypatch):
        monkeypatch.setattr(pr, "LOOP_THRESHOLD", 3)
        monkeypatch.setattr(pr, "LOOP_WINDOW_S", 600)
        monkeypatch.setenv("HOSTNAME", "pod-a")
        db = _FakeDB()
        for _ in range(3):
            await pr.record_boot(db, reason="crash_or_kill")
        trip = db.process_loop_trips.docs[0]
        assert trip["hosts"] == ["pod-a"]
        assert trip["host_counts"] == {"pod-a": 3}
