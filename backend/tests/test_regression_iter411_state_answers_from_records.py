"""
Greetings, a bare "?" and project-issue questions are answered from stored
records, never by a model that can invent scan/review status.
Found by the ORA chat reliability replay (turns 1, 2, 3, 4, 5).
"""
import asyncio

import pytest
from tests._mini_motor import AsyncMongoMockClient

from services.session_state import load_state, render, route_kind


@pytest.mark.parametrize("text,project,kind", [
    ("hi", "p1", "greeting"), ("Hello!", None, "greeting"),
    ("?", "p1", "status"), ("next ?", "p1", "status"),
    ("show me issues", "p1", "issues"), ("any issue in my this project", "p1", "issues"),
    ("any bugs?", "p1", "issues"),
    ("show me issues", None, None),               # no project: not a project question
    ("show me issues", "home", None),
    ("list my github issues", "p1", None),        # GitHub's own tracker is a different ask
    ("close issue #12", "p1", None),
    ("fix the login bug in auth.py and add a test for it today please", "p1", None),
    ("continue", "p1", "continue"),                 # only answered from state when a change is waiting (pre-LLM checks it)
])
def test_regression_iter411_route_kind(text, project, kind):
    assert route_kind(text, project) == kind


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _db(rows=(), pending=0):
    db = AsyncMongoMockClient()["t"]

    async def seed():
        for r in rows:
            await db.cto_open_findings.insert_one(dict(user_id="u", project_id="p", **r))
        for i in range(pending):
            await db.pending_actions.insert_one(
                {"session_id": "s", "user_id": "u", "status": "AWAITING_CONFIRM", "id": str(i)})
    _run(seed())
    return db


def test_regression_iter411_no_records_never_claims_a_clean_scan():
    st = _run(load_state(_db(), user_id="u", project_id="p", session_id="s"))
    for kind in ("greeting", "status", "issues"):
        text = render(kind, st, True)
        assert "No scan results are recorded" in text
        assert "clean" not in text.lower() and "ready for your review" not in text.lower()
    assert "run a scan" in render("issues", st, True)


def test_regression_iter411_issues_come_from_the_database_most_serious_first():
    rows = [{"status": "open", "severity": "low", "title": "L1", "file": "a.py"},
            {"status": "open", "severity": "critical", "title": "C1", "file": "b/c.py"},
            {"status": "fixed", "severity": "high", "title": "gone"}]
    st = _run(load_state(_db(rows), user_id="u", project_id="p", session_id="s"))
    text = render("issues", st, True)
    assert text.index("C1") < text.index("L1") and "gone" not in text
    assert "(b/c.py)" in text and "2 open findings" in text


def test_regression_iter411_all_fixed_is_clean_only_because_a_record_says_so():
    st = _run(load_state(_db([{"status": "fixed", "severity": "high", "title": "x"}]),
                         user_id="u", project_id="p", session_id="s"))
    assert "No open findings are recorded" in render("greeting", st, True)


def test_regression_iter411_waiting_change_is_reported_from_pending_actions():
    st = _run(load_state(_db(pending=2), user_id="u", project_id="p", session_id="s"))
    assert "2 changes waiting for your approval" in render("status", st, True)


def test_regression_iter411_other_users_rows_are_not_shown():
    db = _db([{"status": "open", "severity": "high", "title": "mine"}])
    _run(db.cto_open_findings.insert_one(
        {"user_id": "other", "project_id": "p", "status": "open", "severity": "high", "title": "theirs"}))
    st = _run(load_state(db, user_id="u", project_id="p", session_id="s"))
    assert "theirs" not in render("issues", st, True)


def test_regression_iter411_db_failure_degrades_to_nothing_on_record():
    class Boom:
        def __getattr__(self, _):
            raise RuntimeError("down")
    st = _run(load_state(Boom(), user_id="u", project_id="p", session_id="s"))
    assert st == {"recorded": 0, "open": [], "waiting_changes": 0, "waiting": []}
