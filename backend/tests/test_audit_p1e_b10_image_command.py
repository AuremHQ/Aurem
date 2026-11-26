"""test_audit_p1e_b10_image_command.py — P1 audit fix B10 (2026-11-25).

B10: `/image` was advertised in the safety prompt (Founder-only,
gpt-image-1, 10/month, $3/day) but missing from KNOWN_COMMANDS and
from slash_commands.DISPATCH, so typing it 400'd unknown_command —
while the generator itself already existed (services/ora_chat/
image_gen.py, wired to POST /image-generate). The fix registers
`image` in both places and adds an `_image` handler running the
SAME gate stack as the REST endpoint (founder check → reserve caps
BEFORE the call → generate → refund on failure → audit event).

The traceback half of B10 was already wrong-or-stale (worst case
anywhere in the chat path is str(e)) — so these tests also assert
the negative: no handler result ever contains a traceback, and the
~1 MB base64 payload rides at the top level of the result, never
inside "value" (which /slash feeds to the explain LLM, the
grounding check, and the persisted transcript).

The real image_gen module is used (its OpenAI call is monkeypatched
— no network, no emergentintegrations import: that import is lazy
inside generate() and never reached here).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1e_stubs  # noqa: E402

p1e_stubs.install_p1e_stubs()

from services.ora_chat import image_gen  # noqa: E402
from services.ora_chat import safety  # noqa: E402
from services.ora_chat import slash_commands as sc  # noqa: E402

FOUNDER = {"user_id": "u-founder", "is_founder": True}
NON_FOUNDER = {"user_id": "u-plain", "is_founder": False}

FAKE_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def _run(coro):
    return asyncio.run(coro)


def _fake_generate(monkeypatch, calls, result=None, error=None):
    async def _gen(prompt):
        calls.append(prompt)
        if error is not None:
            raise error
        return {
            "image_base64": FAKE_B64,
            "mime": "image/png",
            "cost_usd": 0.011,
            "prompt": prompt,
            "model": "gpt-image-1",
        }

    monkeypatch.setattr(image_gen, "generate", _gen)


def _db_with_caps(daily_spent=0.0, monthly_used=0):
    db = p1e_stubs.FakeDB()
    if daily_spent:
        db["ora_image_daily_spend"].docs.append(
            {"day": image_gen._utc_today_key(), "spent_usd": daily_spent})
    if monthly_used:
        db["ora_image_user_month"].docs.append(
            {"user_id": "u-founder", "month": image_gen._utc_month_key(),
             "count": monthly_used})
    return db


def _spent(db):
    row = db["ora_image_daily_spend"].docs
    return sum(float(r.get("spent_usd") or 0.0) for r in row)


def _used(db):
    row = db["ora_image_user_month"].docs
    return sum(int(r.get("count") or 0) for r in row)


def _assert_no_traceback(result):
    blob = json.dumps(result, default=str)
    assert "Traceback" not in blob
    assert 'File "' not in blob


# ── Registration ─────────────────────────────────────────────────────
def test_b10_image_in_known_commands_and_parses():
    assert "image" in safety.KNOWN_COMMANDS
    assert safety.parse_slash_command("/image a logo") == ("image", "a logo")
    assert safety.parse_slash_command("/image") == ("image", "")
    assert safety.parse_slash_command("/IMAGE a logo") == ("image", "a logo")


def test_b10_dispatch_covers_every_known_command():
    """The pairing invariant the API layer enforces: every known
    command has a handler, no orphan handlers."""
    assert set(safety.KNOWN_COMMANDS) == set(sc.DISPATCH)
    assert sc.DISPATCH["image"] is sc._image


def test_b10_help_lists_image():
    listing = _run(sc._help({}, ""))
    cmds = [row["cmd"] for row in listing["value"]]
    assert any(c.startswith("/image") for c in cmds)


def test_b10_run_slash_command_dispatches_image(monkeypatch):
    """End-to-end through the registry: no KeyError, no 400 path."""
    db = _db_with_caps()
    monkeypatch.setattr(sc, "get_db", lambda: db)
    calls = []
    _fake_generate(monkeypatch, calls)
    res = _run(sc.run_slash_command("image", "a logo", dict(FOUNDER)))
    assert res["ok"] is True
    assert calls == ["a logo"]


# ── Handler: happy path ──────────────────────────────────────────────
def test_b10_image_generates_and_returns_base64_at_top_level(monkeypatch):
    db = _db_with_caps()
    monkeypatch.setattr(sc, "get_db", lambda: db)
    calls = []
    _fake_generate(monkeypatch, calls)
    res = _run(sc._image(dict(FOUNDER), "a minimal logo"))
    assert res["ok"] is True
    assert res["command"] == "image"
    # The payload the client needs, at the top level …
    assert res["image_base64"] == FAKE_B64
    # … and NOT inside "value": /slash feeds value to the explain
    # LLM, the grounding check, and the persisted transcript, and a
    # ~1 MB base64 blob in any of those would blow them up.
    assert "image_base64" not in res["value"]
    assert "image_base64" not in json.dumps(res["value"])
    assert len(json.dumps(res["value"])) < 2000
    assert res["value"]["model"] == "gpt-image-1"
    assert res["value"]["cost_usd"] == 0.011
    # Caps were reserved exactly once …
    assert _spent(db) == pytest.approx(0.011)
    assert _used(db) == 1
    # … and the audit event was recorded, mirroring /image-generate.
    events = db["ora_image_events"].docs
    assert len(events) == 1
    assert events[0]["prompt"] == "a minimal logo"
    assert events[0]["cost_usd"] == 0.011


# ── Handler: gates ───────────────────────────────────────────────────
def test_b10_image_missing_prompt_is_clean_error(monkeypatch):
    db = _db_with_caps()
    monkeypatch.setattr(sc, "get_db", lambda: db)
    calls = []
    _fake_generate(monkeypatch, calls)
    res = _run(sc._image(dict(FOUNDER), "   "))
    assert res == {"ok": False, "error": "missing_prompt",
                   "hint": "Usage: /image <prompt>"}
    assert calls == []
    _assert_no_traceback(res)


def test_b10_image_non_founder_is_tier_locked(monkeypatch):
    """Same gate as POST /image-generate: Pro/Team get a structured
    refusal, and no quota is reserved."""
    db = _db_with_caps()
    monkeypatch.setattr(sc, "get_db", lambda: db)
    calls = []
    _fake_generate(monkeypatch, calls)
    res = _run(sc._image(dict(NON_FOUNDER), "a logo"))
    assert res["ok"] is False
    assert res["error"] == "tier_locked"
    assert calls == []
    assert _spent(db) == 0.0
    assert _used(db) == 0
    _assert_no_traceback(res)


def test_b10_image_daily_cap_blocks_before_generation(monkeypatch):
    db = _db_with_caps(daily_spent=3.00)
    monkeypatch.setattr(sc, "get_db", lambda: db)
    calls = []
    _fake_generate(monkeypatch, calls)
    res = _run(sc._image(dict(FOUNDER), "a logo"))
    assert res["ok"] is False
    assert res["error"] == "daily_cap_reached"
    assert calls == []  # no OpenAI call, no new reservation
    _assert_no_traceback(res)


def test_b10_image_monthly_cap_blocks_before_generation(monkeypatch):
    db = _db_with_caps(monthly_used=10)
    monkeypatch.setattr(sc, "get_db", lambda: db)
    calls = []
    _fake_generate(monkeypatch, calls)
    res = _run(sc._image(dict(FOUNDER), "a logo"))
    assert res["ok"] is False
    assert res["error"] == "monthly_cap_reached"
    assert calls == []
    _assert_no_traceback(res)


def test_b10_image_upstream_failure_refunds_reservation(monkeypatch):
    """A failed generation must not burn the founder's quota."""
    db = _db_with_caps()
    monkeypatch.setattr(sc, "get_db", lambda: db)
    calls = []
    _fake_generate(monkeypatch, calls,
                   error=image_gen.ImageGenError(
                       "upstream_error", "Image generation failed: 500"))
    res = _run(sc._image(dict(FOUNDER), "a logo"))
    assert res == {"ok": False, "error": "upstream_error",
                   "message": "Image generation failed: 500"}
    assert _spent(db) == pytest.approx(0.0)
    assert _used(db) == 0
    assert db["ora_image_events"].docs == []
    _assert_no_traceback(res)


def test_b10_image_unexpected_exception_is_clean_message_not_traceback(
        monkeypatch):
    """The B10 traceback requirement: even a totally unexpected
    exception surfaces as a short message string — never a
    traceback, never a stack frame."""
    db = _db_with_caps()
    monkeypatch.setattr(sc, "get_db", lambda: db)
    calls = []
    _fake_generate(monkeypatch, calls,
                   error=RuntimeError(
                       "boom\nTraceback (most recent call last):\n"
                       '  File "x.py", line 1, in f\n    raise'))
    res = _run(sc._image(dict(FOUNDER), "a logo"))
    assert res["ok"] is False
    assert res["error"] == "generation_failed"
    assert "boom" in res["message"]
    _assert_no_traceback(res)
    # Quota still refunded on the unexpected path too.
    assert _spent(db) == pytest.approx(0.0)
    assert _used(db) == 0


def test_b10_image_no_db_is_clean_error(monkeypatch):
    monkeypatch.setattr(sc, "get_db", lambda: None)
    res = _run(sc._image(dict(FOUNDER), "a logo"))
    assert res == {"ok": False, "error": "database_unavailable"}
    _assert_no_traceback(res)
