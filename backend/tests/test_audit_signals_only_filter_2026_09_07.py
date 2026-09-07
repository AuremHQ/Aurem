"""
tests/test_audit_signals_only_filter_2026_09_07.py — "Signals only"
toggle on the Admin > Audit feed page (2026-09-07). System signals
(github_auth_failed, repo_not_found, etc.) are genuinely rare — 3 out
of 3,370 turns in the dev DB at the time this was found — so the
default newest-first page almost never shows one even though the
field itself works correctly. `list_turns(signals_only=True)` lets
the admin UI jump straight to the rows that ever had one.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from services import audit_log


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **kw):
        return self

    async def to_list(self, limit):
        return self._docs[:limit]


class _FakeCollection:
    def __init__(self):
        self.last_query = None

    def find(self, q):
        self.last_query = q
        return _FakeCursor([{"turn_id": "t1"}])


def test_list_turns_signals_only_adds_non_empty_array_filter():
    fake_col = _FakeCollection()
    fake_db = type("DB", (), {"__getitem__": lambda self, k: fake_col})()
    with patch("cto_services.db.get_db", return_value=fake_db):
        asyncio.run(audit_log.list_turns(signals_only=True))
    assert fake_col.last_query["system_signals_emitted"] == {"$exists": True, "$ne": []}


def test_list_turns_default_has_no_signals_filter():
    fake_col = _FakeCollection()
    fake_db = type("DB", (), {"__getitem__": lambda self, k: fake_col})()
    with patch("cto_services.db.get_db", return_value=fake_db):
        asyncio.run(audit_log.list_turns())
    assert "system_signals_emitted" not in fake_col.last_query


def test_list_turns_signals_only_combines_with_user_id_filter():
    fake_col = _FakeCollection()
    fake_db = type("DB", (), {"__getitem__": lambda self, k: fake_col})()
    with patch("cto_services.db.get_db", return_value=fake_db):
        asyncio.run(audit_log.list_turns(user_id="u1", signals_only=True))
    assert fake_col.last_query["user_id"] == "u1"
    assert fake_col.last_query["system_signals_emitted"] == {"$exists": True, "$ne": []}
