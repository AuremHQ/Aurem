"""
tests/test_funnel_nudge_throttle_2026_09_08.py — safety throttle for
the nudge-email cron, added alongside the first_chat_at -> first_ship_at
classifier fix.

Why this exists: the classifier fix retroactively surfaces a backlog of
ALREADY-stuck users who were silently hidden by the old bug (confirmed
135 in the sandbox pod alone). Without a floor + a per-run cap, the very
next cron tick after deploy would email that entire backlog in one shot.
Covers:
  * eligible_users() excludes users stuck 24-72h (too fresh for a FIRST
    nudge wave) even though they already clear the 24h STUCK_HOURS gate.
  * eligible_users() includes users stuck >= MIN_NUDGE_STAGE_AGE_HOURS.
  * eligible_users() returns oldest-stuck-first (fair backlog drain).
  * run_nudge_batch() caps at MAX_NUDGES_PER_RUN and reports how many
    were deferred to the next run, without attempting to send to them.
"""
from __future__ import annotations

import time

import pytest


def _matches(doc, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            if "$exists" in v and (k in doc) != v["$exists"]:
                return False
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
            continue
        if doc.get(k) != v:
            return False
    return True


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        v = self._rows[self._i]
        self._i += 1
        return v

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def find(self, query=None, projection=None):
        return _FakeCursor([dict(r) for r in self.rows if _matches(r, query)])

    async def find_one(self, query=None, projection=None):
        matched = [r for r in self.rows if _matches(r, query)]
        return dict(matched[0]) if matched else None


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


@pytest.fixture
def fake_db():
    return _FakeDB()


def _seed_user(db, uid, email, *, days_stuck, has_project=False):
    now = time.time()
    db.dev_users.rows.append({
        "user_id": uid, "email": email, "name": uid,
        "created_at": now - days_stuck * 86400,
    })
    if has_project:
        db.cto_projects.rows.append({"user_id": uid, "project_id": f"p_{uid}"})


async def _no_unsub(db, email):
    return False


@pytest.mark.asyncio
async def test_eligible_users_skips_freshly_stuck_below_min_age(fake_db, monkeypatch):
    from services import funnel_nudge_cron as cron_mod
    monkeypatch.setattr(cron_mod, "_is_unsubscribed", _no_unsub)
    # 2 days stuck: clears the 24h STUCK_HOURS gate but NOT the 72h
    # first-nudge-wave floor.
    _seed_user(fake_db, "u_fresh_stuck", "fresh@x.com", days_stuck=2)
    cohort = await cron_mod.eligible_users(fake_db)
    assert [u["user_id"] for u in cohort] == []


@pytest.mark.asyncio
async def test_eligible_users_includes_users_past_min_age(fake_db, monkeypatch):
    from services import funnel_nudge_cron as cron_mod
    monkeypatch.setattr(cron_mod, "_is_unsubscribed", _no_unsub)
    _seed_user(fake_db, "u_old_stuck", "old@x.com", days_stuck=5)
    cohort = await cron_mod.eligible_users(fake_db)
    assert [u["user_id"] for u in cohort] == ["u_old_stuck"]


@pytest.mark.asyncio
async def test_eligible_users_sorted_oldest_stuck_first(fake_db, monkeypatch):
    from services import funnel_nudge_cron as cron_mod
    monkeypatch.setattr(cron_mod, "_is_unsubscribed", _no_unsub)
    _seed_user(fake_db, "u_5d", "five@x.com", days_stuck=5)
    _seed_user(fake_db, "u_20d", "twenty@x.com", days_stuck=20)
    _seed_user(fake_db, "u_10d", "ten@x.com", days_stuck=10)
    cohort = await cron_mod.eligible_users(fake_db)
    assert [u["user_id"] for u in cohort] == ["u_20d", "u_10d", "u_5d"]


@pytest.mark.asyncio
async def test_run_nudge_batch_caps_per_run_and_defers_the_rest(fake_db, monkeypatch):
    from services import funnel_nudge_cron as cron_mod
    monkeypatch.setattr(cron_mod, "_is_unsubscribed", _no_unsub)
    monkeypatch.setattr(cron_mod, "MAX_NUDGES_PER_RUN", 2)
    for i in range(5):
        _seed_user(fake_db, f"u_{i}", f"u{i}@x.com", days_stuck=10 + i)

    sent_ids = []

    async def _fake_send(user, *, dry_run=False):
        sent_ids.append(user["user_id"])
        return {"ok": True, "user_id": user["user_id"], "email": user["email"], "stage": user["_stage"]}

    monkeypatch.setattr(cron_mod, "send_stage_nudge", _fake_send)
    summary = await cron_mod.run_nudge_batch(fake_db, dry_run=False)

    assert len(sent_ids) == 2  # capped, not all 5
    assert summary["sent"] == 2
    assert summary["deferred_to_next_run"] == 3
    # oldest-stuck-first: u_4 (14d) and u_3 (13d) go out before the rest.
    assert set(sent_ids) == {"u_4", "u_3"}
