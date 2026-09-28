"""
test_master_task_2026_09_28_webhook_signature.py

MASTER TASK (2026-09-28) R1/Part1#1 — full in-process coverage of the
GitHub webhook signature gate that test_iter78_automations.py's live-
server tests can't exercise (that test hits an already-running server
whose `GITHUB_WEBHOOK_SECRET` module constant was read once at import
time and can't be changed without a restart). Here we import the
router module directly and monkeypatch its `GITHUB_WEBHOOK_SECRET`
constant, so all four states are covered:
  1. No secret configured           -> 403 (verified live in the other
     test file too — this is the actual production bug that existed).
  2. Secret configured, no signature -> 401.
  3. Secret configured, wrong sig    -> 401.
  4. Secret configured, correct sig  -> 200, processes normally.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import automations as auto_router   # noqa: E402


def _fake_request(body: bytes) -> SimpleNamespace:
    async def _body():
        return body
    return SimpleNamespace(body=_body)


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_403_when_secret_unset(monkeypatch):
    monkeypatch.setattr(auto_router, "GITHUB_WEBHOOK_SECRET", "")
    body = json.dumps({"repository": {"full_name": "x/y"}}).encode()
    with pytest.raises(Exception) as exc_info:
        await auto_router.github_webhook(
            _fake_request(body), x_hub_signature_256=None, x_github_event="push",
        )
    assert getattr(exc_info.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_webhook_401_on_missing_signature_when_secret_set(monkeypatch):
    monkeypatch.setattr(auto_router, "GITHUB_WEBHOOK_SECRET", "s3cr3t")
    body = json.dumps({"repository": {"full_name": "x/y"}}).encode()
    with pytest.raises(Exception) as exc_info:
        await auto_router.github_webhook(
            _fake_request(body), x_hub_signature_256=None, x_github_event="push",
        )
    assert getattr(exc_info.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_webhook_401_on_wrong_signature_when_secret_set(monkeypatch):
    monkeypatch.setattr(auto_router, "GITHUB_WEBHOOK_SECRET", "s3cr3t")
    body = json.dumps({"repository": {"full_name": "x/y"}}).encode()
    with pytest.raises(Exception) as exc_info:
        await auto_router.github_webhook(
            _fake_request(body), x_hub_signature_256="sha256=deadbeef",
            x_github_event="push",
        )
    assert getattr(exc_info.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_webhook_200_with_correct_signature_push_event(monkeypatch):
    monkeypatch.setattr(auto_router, "GITHUB_WEBHOOK_SECRET", "s3cr3t")
    payload = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "acme/repo"},
        "pusher": {"name": "octocat"},
        "commits": [{"message": "fix: real signed push"}],
    }
    body = json.dumps(payload).encode()
    sig = _sign("s3cr3t", body)

    fake_db = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(return_value=[])   # no matching automation rules
    fake_db.cto_automations.find = MagicMock(return_value=fake_cursor)
    monkeypatch.setattr(auto_router, "require_db", lambda: fake_db)

    result = await auto_router.github_webhook(
        _fake_request(body), x_hub_signature_256=sig, x_github_event="push",
    )
    assert result["ok"] is True
    assert result["triggered"] == 0     # no rules matched — that's fine, proves it got PAST the gate


@pytest.mark.asyncio
async def test_webhook_200_skipped_for_non_push_with_correct_signature(monkeypatch):
    monkeypatch.setattr(auto_router, "GITHUB_WEBHOOK_SECRET", "s3cr3t")
    body = json.dumps({"zen": "..."}).encode()
    sig = _sign("s3cr3t", body)
    result = await auto_router.github_webhook(
        _fake_request(body), x_hub_signature_256=sig, x_github_event="ping",
    )
    assert result == {"ok": True, "skipped": True, "event": "ping"}


# ── Ship Wall flag gate (Part1#5) ─────────────────────────────────────

class _EmptyAgg:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def to_list(self, n):
        return []


@pytest.mark.asyncio
async def test_wall_endpoints_404_when_flag_off(monkeypatch):
    from routers import shipwall
    monkeypatch.setattr("services.feature_flags.is_enabled", AsyncMock(return_value=False))
    for coro in (
        shipwall.ship_wall_feed(limit=10),
        shipwall.wall_stats(),
        shipwall.developer_wall("someone"),
        shipwall.readme_badge("u1"),
    ):
        with pytest.raises(Exception) as exc_info:
            await coro
        assert getattr(exc_info.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_wall_stats_200_when_flag_on(monkeypatch):
    from routers import shipwall
    monkeypatch.setattr("services.feature_flags.is_enabled", AsyncMock(return_value=True))
    fake_db = MagicMock()
    fake_db.cto_tasks.count_documents = AsyncMock(return_value=0)
    fake_db.cto_tasks.aggregate = MagicMock(return_value=_EmptyAgg())
    monkeypatch.setattr(shipwall, "get_db", lambda: fake_db)
    result = await shipwall.wall_stats()
    assert "total_ships" in result


# ── Automations create flag gate (Part1#3) ────────────────────────────

@pytest.mark.asyncio
async def test_automations_create_403_when_flag_off(monkeypatch):
    monkeypatch.setattr(
        "routers.automations.current_dev",
        AsyncMock(return_value={"user_id": "u1"}),
    )
    monkeypatch.setattr("routers.automations.require_db", lambda: MagicMock())
    monkeypatch.setattr("services.feature_flags.is_enabled", AsyncMock(return_value=False))
    with pytest.raises(Exception) as exc_info:
        await auto_router.create_automation(
            {"name": "x", "repo_full_name": "a/b", "trigger": "push",
             "task_template": "t"},
            authorization="Bearer fake",
        )
    assert getattr(exc_info.value, "status_code", None) == 403
