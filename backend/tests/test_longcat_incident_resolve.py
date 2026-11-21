"""Guard 20 resolve-gap fix — LongCat stuck-degraded incident (2026-11-21).

Iter 363 opened `longcat_stuck_degraded` after 20 consecutive failed
reprobes but nothing ever resolved it: the incident stayed OPEN
forever after upstream recovered, dragging the health-score Incident
Rate (-10 per open incident) and Reliability G20 (-20 per open
incident) down. These tests lock the fix:
  1. the resolve helper closes an open incident (and no-ops when none)
  2. the reprobe loop resolves it on the DEGRADED -> LIVE transition
     after a stuck period (driven loop, stubbed probe + sleep).
"""
from __future__ import annotations

import asyncio

import pytest

import cto_services.db as cto_db
from services.llm import _probes as probes


def _match_eq(doc, query):
    for k, cond in query.items():
        if isinstance(cond, dict):
            if "$ne" in cond and doc.get(k) == cond["$ne"]:
                return False
            if "$gte" in cond and not (
                doc.get(k) is not None and doc.get(k) >= cond["$gte"]
            ):
                return False
        elif doc.get(k) != cond:
            return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, _key, _direction):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n):
        return [dict(d) for d in self._docs[:n]]


class _FakeIncidentColl:
    """Minimal incidents collection: the exact calls `list_incidents`
    + `resolve_incident` make (find / find_one / update_one)."""
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query, proj=None):
        return _FakeCursor([r for r in self.rows if _match_eq(r, query)])

    async def find_one(self, query, proj=None):
        for r in self.rows:
            if _match_eq(r, query):
                return dict(r)
        return None

    async def update_one(self, query, update):
        for r in self.rows:
            if _match_eq(r, query):
                for k, v in update.get("$set", {}).items():
                    r[k] = v
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


def _open_longcat_row():
    import time
    return {
        "incident_id": "inc_longcat_stuck_degraded",
        "source_key": "longcat_stuck_degraded",
        "status": "open",
        "detected_at": time.time() - 3600,
        "resolution": None,
    }


class _FakeDB:
    def __init__(self, rows=None):
        self.incidents = _FakeIncidentColl(rows)


@pytest.mark.asyncio
async def test_resolve_helper_closes_open_incident(monkeypatch):
    """The resolve helper closes an open stuck-degraded incident."""
    db = _FakeDB([_open_longcat_row()])
    monkeypatch.setattr(cto_db, "get_db", lambda: db)

    await probes._resolve_stuck_degraded_if_open()

    row = db.incidents.rows[0]
    assert row["status"] == "resolved"
    assert "healthy again" in row["resolution"]


@pytest.mark.asyncio
async def test_resolve_helper_noops_when_none_open(monkeypatch):
    """No open incident → helper returns quietly, no crash."""
    db = _FakeDB([])
    monkeypatch.setattr(cto_db, "get_db", lambda: db)

    await probes._resolve_stuck_degraded_if_open()  # must not raise


@pytest.mark.asyncio
async def test_reprobe_loop_resolves_on_recovery_after_stuck(
    monkeypatch,
):
    """Drive the real loop: 20 failed probes (stuck) then recovery.
    The incident opened at the stuck point must be RESOLVED on the
    DEGRADED -> LIVE transition — that is the fix."""
    db = _FakeDB([_open_longcat_row()])
    monkeypatch.setattr(cto_db, "get_db", lambda: db)
    monkeypatch.setattr(probes, "LONGCAT_ENABLED", True)
    monkeypatch.setattr(probes, "LONGCAT_LIVE", True)

    script = [False] * 20 + [True] * 10
    calls = {"n": 0}

    async def _fake_probe():
        v = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        probes.LONGCAT_LIVE = v
        return v

    monkeypatch.setattr(
        probes, "probe_longcat_availability", _fake_probe)

    sleeps = {"n": 0}

    async def _fake_sleep(_seconds):
        # No real waiting: run iterations back-to-back, then exit the
        # infinite loop the same way a shutdown would.
        sleeps["n"] += 1
        if sleeps["n"] >= 25:
            raise asyncio.CancelledError

    monkeypatch.setattr(probes.asyncio, "sleep", _fake_sleep)

    await probes.periodic_longcat_reprobe(interval_seconds=900)

    assert calls["n"] >= 21, "loop must reach the recovery probe"
    row = db.incidents.rows[0]
    assert row["source_key"] == "longcat_stuck_degraded"
    assert row["status"] == "resolved"
    assert "healthy again" in row["resolution"]


@pytest.mark.asyncio
async def test_reprobe_loop_no_resolve_after_short_blip(monkeypatch):
    """A short degradation (fewer than 20 failed probes) never opened
    an incident, so recovery must not write a resolution anywhere."""
    db = _FakeDB([])
    monkeypatch.setattr(cto_db, "get_db", lambda: db)
    monkeypatch.setattr(probes, "LONGCAT_ENABLED", True)
    monkeypatch.setattr(probes, "LONGCAT_LIVE", True)

    script = [False] * 5 + [True] * 10
    calls = {"n": 0}

    async def _fake_probe():
        v = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        probes.LONGCAT_LIVE = v
        return v

    monkeypatch.setattr(
        probes, "probe_longcat_availability", _fake_probe)

    sleeps = {"n": 0}

    async def _fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 10:
            raise asyncio.CancelledError

    monkeypatch.setattr(probes.asyncio, "sleep", _fake_sleep)

    await probes.periodic_longcat_reprobe(interval_seconds=900)

    assert db.incidents.rows == []
