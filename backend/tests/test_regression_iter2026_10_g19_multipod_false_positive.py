"""tests/test_regression_iter2026_10_g19_multipod_false_positive.py

Deploy-health bug fix (2026-10): `_classify_boot_reason` compared each
new boot against the GLOBALLY most-recent `process_boots` row with no
host/pod scoping. On a multi-replica deployment, a brand-new sibling
pod booting a few seconds after another (same sha, no deploy event,
and no shutdown marker because the OTHER pod is still running) was
misclassified `crash_or_kill` — with 2+ replicas starting together
that alone can hit `LOOP_THRESHOLD` and fire a false "RESTART LOOP
DETECTED" CRITICAL incident on an entirely healthy rollout.

This locks in: boots are classified per-`host` (K8s's `HOSTNAME`), so
sibling pods never poison each other's classification, while a REAL
same-pod crash loop (no host change) is still caught.
"""
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

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if self._match(d, query):
                self._apply(d, update)
                return
        if upsert:
            d = {k: v for k, v in query.items() if not isinstance(v, dict)}
            self._apply(d, update)
            self.docs.append(d)

    async def update_many(self, query, update):
        for d in self.docs:
            if self._match(d, query):
                self._apply(d, update)

    @staticmethod
    def _apply(d, update):
        for k, v in update.get("$set", {}).items():
            d[k] = v
        for k, v in update.get("$setOnInsert", {}).items():
            d.setdefault(k, v)
        for k, v in update.get("$inc", {}).items():
            d[k] = d.get(k, 0) + v

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


class _FakeDB:
    def __init__(self):
        self.process_boots = _FakeCollection()
        self.process_loop_trips = _FakeCollection()
        self.topup_alerts = _FakeCollection()
        self.process_shutdown_marker = _FakeCollection()


@pytest.mark.asyncio
async def test_sibling_pods_booting_together_never_trip_loop(monkeypatch):
    """3 DIFFERENT pods each booting once (normal multi-replica
    rollout, same sha, no deploy in between) must never classify as
    crash_or_kill — each pod has no prior boot under its OWN host."""
    monkeypatch.setattr(pr, "LOOP_THRESHOLD", 3)
    monkeypatch.setattr(pr, "LOOP_WINDOW_S", 600)
    db = _FakeDB()
    for pod in ("pod-a", "pod-b", "pod-c"):
        monkeypatch.setenv("HOSTNAME", pod)
        out = await pr.record_boot(db, reason="supervisor_start")
    reasons = [d["reason"] for d in db.process_boots.docs]
    assert reasons == ["first_boot", "first_boot", "first_boot"]
    assert out["loop_detected"] is False
    assert len(db.process_loop_trips.docs) == 0


@pytest.mark.asyncio
async def test_same_pod_repeated_crash_still_detected(monkeypatch):
    """A REAL crash loop — the SAME pod restarting without ever
    recording a clean shutdown — must still trip. First boot is
    always `first_boot` (no prior boot to compare against), so 4
    total boots are needed to reach 3 `crash_or_kill` classifications."""
    monkeypatch.setattr(pr, "LOOP_THRESHOLD", 3)
    monkeypatch.setattr(pr, "LOOP_WINDOW_S", 600)
    monkeypatch.setenv("HOSTNAME", "pod-a")
    db = _FakeDB()
    out = None
    for _ in range(4):
        out = await pr.record_boot(db, reason="supervisor_start")
    reasons = [d["reason"] for d in db.process_boots.docs]
    assert reasons == ["first_boot", "crash_or_kill", "crash_or_kill", "crash_or_kill"]
    assert out["loop_detected"] is True


@pytest.mark.asyncio
async def test_same_pod_graceful_restart_classified_clean(monkeypatch):
    """A pod that calls mark_clean_shutdown before restarting (e.g. a
    supervisor-managed restart) must classify as clean_restart, not
    crash_or_kill — and a sibling pod's marker must never satisfy
    this check for a DIFFERENT host."""
    monkeypatch.setenv("HOSTNAME", "pod-a")
    db = _FakeDB()
    await pr.record_boot(db, reason="supervisor_start")
    await pr.mark_clean_shutdown(db)
    out = await pr.record_boot(db, reason="supervisor_start")
    assert out["recorded"] is True
    assert db.process_boots.docs[-1]["reason"] == "clean_restart"

    # A sibling pod's marker (different host) must not leak across.
    monkeypatch.setenv("HOSTNAME", "pod-b")
    await pr.record_boot(db, reason="supervisor_start")
    assert db.process_boots.docs[-1]["reason"] == "first_boot"
