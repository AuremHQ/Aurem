"""
The ORA grounding canary stayed silent when ORA gave no answer
(`canary:codebase_specific` had no assistant reply for two days).
Found by the ORA chat reliability replay (H7).
"""
from unittest.mock import AsyncMock

import pytest

from services.ora_chat import canary


def _arm(monkeypatch, texts):
    sent, alerts, persisted = [], [], []

    async def token():
        return "tok", "founder@example.com"

    async def send(client, headers, sid, content):
        sent.append(content)
        return {"text": texts(content), "fabricated": []}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"session": {"session_id": f"s{len(sent)}"}}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp()

    async def alert(report, email): alerts.append((report, email))
    async def persist(report): persisted.append(report)

    monkeypatch.setattr(canary, "_founder_token", token)
    monkeypatch.setattr(canary, "_send_message", send)
    monkeypatch.setattr(canary.httpx, "AsyncClient", lambda **k: _Client())
    monkeypatch.setattr(canary, "_alert", alert)
    monkeypatch.setattr(canary, "_persist", persist)
    monkeypatch.setattr("services.ora_chat.adversarial_review.run_review",
                        AsyncMock(return_value={"flags": [{"type": "x"}]}))
    return alerts, persisted


@pytest.mark.asyncio
async def test_regression_iter413_empty_reply_on_any_prompt_fails_and_alerts(monkeypatch):
    # every prompt answers except codebase_specific, which gets nothing
    alerts, _ = _arm(monkeypatch, lambda c: "" if "codebase_index.py" in c else
                     "I haven't read it; unverified. " + c[:20])
    report = await canary.run_canary(triggered_by="test")
    row = next(r for r in report["results"] if r.get("name") == "codebase_specific")
    assert row["error"] == "empty_reply"
    assert report["ok"] is False
    assert len(alerts) == 1 and alerts[0][1] == "founder@example.com"


@pytest.mark.asyncio
async def test_regression_iter413_all_answers_present_does_not_alert_for_silence(monkeypatch):
    alerts, _ = _arm(monkeypatch, lambda c: "I haven't read it; unverified. Details here.")
    report = await canary.run_canary(triggered_by="test")
    assert not any(r.get("error") == "empty_reply" for r in report["results"])
    assert alerts == [] and report["ok"] is True


@pytest.mark.asyncio
async def test_regression_iter413_cannot_start_is_alerted_not_just_stored(monkeypatch):
    alerts, persisted = _arm(monkeypatch, lambda c: "x")

    async def broken():
        raise RuntimeError("founder_not_configured")
    monkeypatch.setattr(canary, "_founder_token", broken)
    monkeypatch.setattr(canary, "_any_founder_email", lambda: "owner@example.com")
    report = await canary.run_canary(triggered_by="test")
    assert "founder_not_configured" in report["error"]
    assert persisted and len(alerts) == 1 and alerts[0][1] == "owner@example.com"
