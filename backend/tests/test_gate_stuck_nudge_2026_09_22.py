"""
tests/test_gate_stuck_nudge_2026_09_22.py — fast "stuck at the
repo-connect gate" nudge (services/gate_stuck_nudge.py).

Real activity-log review found two traces that looked identical in the
funnel log because `chat_opened` fired regardless of gate state:
  * User 1: github_auth_started -> connect_repo_click ->
    app_install_redirect -> repo_connect_gate_shown -> chat_opened.
    No skip, no install, no project — genuinely stuck at the gate.
  * User 2: installed the GitHub App, then explicitly
    repo_connect_gate_skipped -> chat_opened. Resolved on purpose.

This suite covers the classifier `stuck_gate_users()` distinguishing
those two cases, plus the batch's dedup/unsubscribe/cap behavior.
`chat_opened` timing itself is a frontend (Dashboard.jsx/ChatPanel.jsx)
fix, verified separately by the testing agent.
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

    async def distinct(self, field, query=None):
        matched = [r for r in self.rows if _matches(r, query)]
        out, seen = [], set()
        for r in matched:
            v = r.get(field)
            if v is not None and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    async def insert_one(self, doc):
        from pymongo.errors import DuplicateKeyError
        # Same uniq_user_campaign_stage semantics as production: one
        # row per (user_id, campaign, stage).
        for r in self.rows:
            if (r.get("user_id"), r.get("campaign"), r.get("stage")) == \
               (doc.get("user_id"), doc.get("campaign"), doc.get("stage")):
                raise DuplicateKeyError("dup")
        doc = dict(doc)
        doc["_id"] = f"id_{len(self.rows)}"
        self.rows.append(doc)

        class _Res:
            inserted_id = doc["_id"]
        return _Res()

    async def update_one(self, query, update):
        for r in self.rows:
            if _matches(r, query):
                r.update(update.get("$set", {}))
                return


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


def _seed_user(db, uid, email, *, name=None, skipped=False):
    db.dev_users.rows.append({
        "user_id": uid, "email": email, "name": name or uid,
        "repo_connect_gate_skipped": skipped,
    })


def _seed_started_event(db, uid, *, hours_ago, stage="github_auth_started"):
    db.github_funnel_events.rows.append({
        "user_id": uid, "stage": stage, "ts": time.time() - hours_ago * 3600,
    })


async def _no_unsub(db, email):
    return False


@pytest.mark.asyncio
async def test_stuck_gate_users_includes_started_no_skip_no_project(fake_db, monkeypatch):
    """User 1's exact trace: started the flow, no skip, no project,
    stuck well past the window."""
    from services import gate_stuck_nudge as mod
    monkeypatch.setattr(mod, "GATE_STUCK_HOURS", 4.0)
    _seed_user(fake_db, "u1", "u1@x.com")
    _seed_started_event(fake_db, "u1", hours_ago=6)

    cohort = await mod.stuck_gate_users(fake_db)
    assert [u["user_id"] for u in cohort] == ["u1"]


@pytest.mark.asyncio
async def test_stuck_gate_users_excludes_explicit_skip(fake_db, monkeypatch):
    """User 2's trace: started, then explicitly skipped — resolved,
    must NOT be treated as stuck."""
    from services import gate_stuck_nudge as mod
    monkeypatch.setattr(mod, "GATE_STUCK_HOURS", 4.0)
    _seed_user(fake_db, "u2", "u2@x.com", skipped=True)
    _seed_started_event(fake_db, "u2", hours_ago=6)

    cohort = await mod.stuck_gate_users(fake_db)
    assert cohort == []


@pytest.mark.asyncio
async def test_stuck_gate_users_excludes_already_connected(fake_db, monkeypatch):
    from services import gate_stuck_nudge as mod
    monkeypatch.setattr(mod, "GATE_STUCK_HOURS", 4.0)
    _seed_user(fake_db, "u3", "u3@x.com")
    _seed_started_event(fake_db, "u3", hours_ago=6)
    fake_db.cto_projects.rows.append({"user_id": "u3", "project_id": "p1"})

    cohort = await mod.stuck_gate_users(fake_db)
    assert cohort == []


@pytest.mark.asyncio
async def test_stuck_gate_users_excludes_too_fresh(fake_db, monkeypatch):
    """Started only 1h ago with a 4h window — not stuck long enough yet."""
    from services import gate_stuck_nudge as mod
    monkeypatch.setattr(mod, "GATE_STUCK_HOURS", 4.0)
    _seed_user(fake_db, "u4", "u4@x.com")
    _seed_started_event(fake_db, "u4", hours_ago=1)

    cohort = await mod.stuck_gate_users(fake_db)
    assert cohort == []


@pytest.mark.asyncio
async def test_stuck_gate_users_excludes_never_started(fake_db, monkeypatch):
    """No connect-flow signal at all — not this cron's job."""
    from services import gate_stuck_nudge as mod
    monkeypatch.setattr(mod, "GATE_STUCK_HOURS", 4.0)
    _seed_user(fake_db, "u5", "u5@x.com")

    cohort = await mod.stuck_gate_users(fake_db)
    assert cohort == []


@pytest.mark.asyncio
async def test_run_batch_skips_already_sent(fake_db, monkeypatch):
    from services import gate_stuck_nudge as mod
    import services.first50_campaign as fc_mod
    monkeypatch.setattr(mod, "GATE_STUCK_HOURS", 4.0)
    monkeypatch.setattr(fc_mod, "is_unsubscribed", _no_unsub)
    _seed_user(fake_db, "u6", "u6@x.com")
    _seed_started_event(fake_db, "u6", hours_ago=6)
    fake_db.onboarding_emails.rows.append({
        "user_id": "u6", "campaign": mod.CAMPAIGN, "stage": mod.CAMPAIGN,
    })

    sent_ids = []

    async def _fake_send(user, *, dry_run=False):
        sent_ids.append(user["user_id"])
        return {"ok": True, "user_id": user["user_id"], "email": user["email"]}

    monkeypatch.setattr(mod, "send_gate_stuck_nudge", _fake_send)
    summary = await mod.run_gate_stuck_nudge_batch(fake_db, dry_run=False)

    assert sent_ids == []
    assert summary["sent"] == 0


@pytest.mark.asyncio
async def test_run_batch_sends_to_eligible_user(fake_db, monkeypatch):
    from services import gate_stuck_nudge as mod
    import services.first50_campaign as fc_mod
    monkeypatch.setattr(mod, "GATE_STUCK_HOURS", 4.0)
    monkeypatch.setattr(fc_mod, "is_unsubscribed", _no_unsub)
    _seed_user(fake_db, "u7", "u7@x.com")
    _seed_started_event(fake_db, "u7", hours_ago=6)

    sent_ids = []

    async def _fake_send(user, *, dry_run=False):
        sent_ids.append(user["user_id"])
        return {"ok": True, "user_id": user["user_id"], "email": user["email"]}

    monkeypatch.setattr(mod, "send_gate_stuck_nudge", _fake_send)
    summary = await mod.run_gate_stuck_nudge_batch(fake_db, dry_run=False)

    assert sent_ids == ["u7"]
    assert summary["sent"] == 1


@pytest.mark.asyncio
async def test_run_batch_caps_per_run(fake_db, monkeypatch):
    from services import gate_stuck_nudge as mod
    import services.first50_campaign as fc_mod
    monkeypatch.setattr(mod, "GATE_STUCK_HOURS", 4.0)
    monkeypatch.setattr(mod, "MAX_NUDGES_PER_RUN", 2)
    monkeypatch.setattr(fc_mod, "is_unsubscribed", _no_unsub)
    for i in range(5):
        _seed_user(fake_db, f"c{i}", f"c{i}@x.com")
        _seed_started_event(fake_db, f"c{i}", hours_ago=6 + i)

    sent_ids = []

    async def _fake_send(user, *, dry_run=False):
        sent_ids.append(user["user_id"])
        return {"ok": True, "user_id": user["user_id"], "email": user["email"]}

    monkeypatch.setattr(mod, "send_gate_stuck_nudge", _fake_send)
    summary = await mod.run_gate_stuck_nudge_batch(fake_db, dry_run=False)

    assert len(sent_ids) == 2
    assert summary["deferred_to_next_run"] == 3
    # oldest-stuck-first: c4 (10h) and c3 (9h) go first.
    assert set(sent_ids) == {"c4", "c3"}
