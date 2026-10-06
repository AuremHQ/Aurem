"""Admin funnel "Sent first message = 0": only /chat/send stamped
`first_chat_sent`; the real send path (/chat/stream) never did.  Both paths
now share `stamp_first_chat`.  Behavioural test of the helper (idempotent,
emits exactly once, never raises); the wiring assertion below is a static
guard and is NOT the proof — the helper behaviour is."""
from __future__ import annotations

import asyncio
from pathlib import Path

from services.signup_guards import stamp_first_chat


class _Users:
    def __init__(self):
        self.row = {"user_id": "u"}

    async def find_one_and_update(self, q, upd, projection=None):
        if "first_chat_at" in self.row:
            return None
        self.row.update(upd["$set"])
        return {"user_id": "u"}


class _Events:
    def __init__(self):
        self.rows = []

    async def insert_one(self, d):
        self.rows.append(d)


class _DB:
    def __init__(self):
        self.dev_users, self.funnel_events = _Users(), _Events()


def test_first_message_stamps_once_and_emits_once():
    db = _DB()
    assert asyncio.run(stamp_first_chat(db, user_id="u", provider="p", mode="A")) is True
    assert asyncio.run(stamp_first_chat(db, user_id="u")) is False
    assert [e["event_type"] for e in db.funnel_events.rows] == ["first_chat_sent"]
    assert "first_chat_at" in db.dev_users.row


def test_never_raises_on_db_failure_or_missing_args():
    class _Bad:
        class dev_users:
            @staticmethod
            async def find_one_and_update(*a, **k):
                raise RuntimeError("down")
    assert asyncio.run(stamp_first_chat(_Bad, user_id="u")) is False
    assert asyncio.run(stamp_first_chat(None, user_id="u")) is False
    assert asyncio.run(stamp_first_chat(_DB(), user_id="")) is False


def test_stream_path_is_wired_to_the_helper():
    src = (Path(__file__).resolve().parents[1] / "routers" / "chat" / "stream.py").read_text()
    assert "stamp_first_chat" in src
