"""Regression — GET /github/app/callback must not hand an installation to another account.

`/callback` is unauthenticated (GitHub redirects the browser to it). When the
state row was missing it used to recover the user id by parsing the STATE STRING
(`gha:<user_id>:<anything>`) and `upsert_installation` then overwrote the
installation's owner. So `GET /callback?installation_id=<victim's>&state=gha:<attacker>:x`
re-pointed the victim's GitHub App installation at the attacker, who could then add
the victim's private repos as projects (`verify_installation_access` only checks
`github_installations.user_id`).

Now: the user is recovered only from a state row /install actually stored, an
installation owned by a different account is never re-pointed by the callback, and
`upsert_installation` refuses to move an existing owner as a last line of defence.

These tests call the real `install_callback` / `upsert_installation` with an
in-memory DB and stubbed GitHub calls, so they need neither Mongo nor a server.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.requests import Request  # noqa: E402

# Imported lazily (module fixture below), NOT at collection time: importing
# `services.github_app` while pytest collects sets it as an attribute of the
# `services` package, and tests/test_iter388t_self_delete.py's
# `monkeypatch.setitem(sys.modules, "services.github_app", ...)` then stops
# working for every test run later in the same process.
ga = None
svc = None


@pytest.fixture(autouse=True, scope="module")
def _load_github_app_modules():
    global ga, svc
    import routers.github_app as _ga
    from services import github_app as _svc
    ga, svc = _ga, _svc
    yield

VICTIM, ATTACKER, IID = "victim_user_id_111", "attacker_user_id_222", 424242
FUTURE = 9_999_999_999


class _Coll:
    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]

    @staticmethod
    def _match(row, filt):
        return all(row.get(k) == v for k, v in filt.items() if not isinstance(v, dict))

    async def find_one(self, filt, proj=None):
        for r in self.rows:
            if self._match(r, filt):
                return dict(r)
        return None

    async def find_one_and_update(self, filt, upd, upsert=False, return_document=None):
        for r in self.rows:
            if self._match(r, filt):
                r.update(upd.get("$set", {}))
                return dict(r)
        if upsert:
            new = {
                **{k: v for k, v in filt.items() if not isinstance(v, dict)},
                **upd.get("$setOnInsert", {}),
                **upd.get("$set", {}),
            }
            self.rows.append(new)
            return dict(new)
        return None

    async def update_one(self, filt, upd):
        for r in self.rows:
            if self._match(r, filt):
                r.update(upd.get("$set", {}))
                return None
        return None

    async def update_many(self, *a, **k):
        return None

    async def insert_one(self, doc):
        self.rows.append(doc)


class _DB:
    def __init__(self, installations=(), states=()):
        self.oauth_states = _Coll(states)
        self.github_installations = _Coll(installations)
        self.cto_projects = _Coll()


def _owner(db):
    return db.github_installations.rows[0].get("user_id")


def _run_callback(monkeypatch, db, *, state, setup_action="install"):
    async def meta(_iid):
        return {"account": {"login": "victim-org", "type": "Organization", "id": 9},
                "repository_selection": "all", "permissions": {}, "events": []}

    async def repos(_iid):
        return [{"full_name": "victim-org/secret-repo", "private": True}]

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(ga, "require_db", lambda: db)
    monkeypatch.setattr(ga, "_app_configured", lambda: True)
    monkeypatch.setattr(ga, "_fetch_installation_meta", meta)
    monkeypatch.setattr(ga._ga, "list_installation_repos", repos)
    monkeypatch.setattr(ga, "_funnel_track", noop)
    req = Request({"type": "http", "method": "GET", "path": "/callback",
                   "headers": [(b"host", b"aurem.test")], "query_string": b""})
    return asyncio.run(ga.install_callback(
        req, installation_id=IID, setup_action=setup_action, state=state, code=None))


def _state_row(user_id, token="tok", used=False):
    return {"state": f"gha:{user_id}:{token}", "kind": "github_app_install",
            "user_id": user_id, "used": used, "expires_at": FUTURE}


def test_regression_iter402_forged_state_cannot_take_an_owned_installation(monkeypatch):
    db = _DB(installations=[{"installation_id": IID, "user_id": VICTIM, "active": True}])
    _run_callback(monkeypatch, db, state=f"gha:{ATTACKER}:not-a-real-token")
    assert _owner(db) == VICTIM


def test_regression_iter402_forged_state_cannot_claim_an_unowned_ghost_installation(monkeypatch):
    db = _DB(installations=[{"installation_id": IID, "user_id": None, "active": True}])
    resp = _run_callback(monkeypatch, db, state=f"gha:{ATTACKER}:not-a-real-token")
    assert _owner(db) is None
    assert "invalid_state" in resp.headers["location"]


def test_regression_iter402_valid_state_still_links_a_new_installation(monkeypatch):
    db = _DB(states=[_state_row(ATTACKER)])
    resp = _run_callback(monkeypatch, db, state=f"gha:{ATTACKER}:tok")
    assert _owner(db) == ATTACKER
    assert "status=success" in resp.headers["location"]


def test_regression_iter402_double_fire_recovers_from_the_stored_used_row(monkeypatch):
    """GitHub sometimes calls back twice: the second call finds the row already
    used. The user is recovered from that stored row, not from the string."""
    db = _DB(states=[_state_row(ATTACKER, used=True)])
    resp = _run_callback(monkeypatch, db, state=f"gha:{ATTACKER}:tok")
    assert _owner(db) == ATTACKER
    assert "status=success" in resp.headers["location"]


def test_regression_iter402_valid_state_of_another_user_does_not_move_an_owned_installation(monkeypatch):
    db = _DB(installations=[{"installation_id": IID, "user_id": VICTIM, "active": True}],
             states=[_state_row(ATTACKER)])
    resp = _run_callback(monkeypatch, db, state=f"gha:{ATTACKER}:tok")
    assert _owner(db) == VICTIM
    assert "invalid_state" in resp.headers["location"]


def test_regression_iter402_no_state_reuses_the_existing_link(monkeypatch):
    """GitHub drops `state` when an already-installed user revisits; that is a
    re-confirmation of the existing owner and must keep working."""
    db = _DB(installations=[{"installation_id": IID, "user_id": VICTIM, "active": True}])
    resp = _run_callback(monkeypatch, db, state=None)
    assert _owner(db) == VICTIM
    assert "status=success" in resp.headers["location"]


def test_regression_iter402_upsert_never_reassigns_an_existing_owner():
    db = _DB(installations=[{"installation_id": IID, "user_id": VICTIM, "active": True}])
    asyncio.run(svc.upsert_installation(db, IID, {"account": {"login": "x"}}, user_id=ATTACKER))
    assert _owner(db) == VICTIM


def test_regression_iter402_upsert_still_sets_the_owner_of_an_unowned_row():
    db = _DB(installations=[{"installation_id": IID, "user_id": None, "active": True}])
    asyncio.run(svc.upsert_installation(db, IID, {"account": {"login": "x"}}, user_id=ATTACKER))
    assert _owner(db) == ATTACKER


@pytest.mark.parametrize("bad", ["", "gha:", "gha::x", "gha:only-two-parts"])
def test_regression_iter402_malformed_states_never_link(monkeypatch, bad):
    db = _DB(installations=[{"installation_id": IID, "user_id": None, "active": True}])
    _run_callback(monkeypatch, db, state=bad)
    assert _owner(db) is None
