"""
tests/test_signal_alerts_2026_09_07.py — "Signal Alerts" (2026-09-07).
Ping the founder the moment a real ORA system signal fires (
github_auth_failed, repo_not_found, etc.) instead of them checking
the Audit feed's "Signals only" toggle manually. Reuses the existing
Resend-based `services.founder_alerts.send_founder_alert` channel.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services import audit_log


class _FakeCollection:
    def __init__(self):
        self.rows = []

    async def insert_one(self, doc):
        self.rows.append(doc)


class _FakeDB:
    def __init__(self):
        self.ora_audit = _FakeCollection()

    def __getitem__(self, name):
        return getattr(self, name)


async def _drain_tasks():
    """record_turn schedules the alert via asyncio.create_task —
    give the event loop one tick so it actually runs before we
    assert on it."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _run_and_drain(**kwargs):
    turn_id = await audit_log.record_turn(**kwargs)
    await _drain_tasks()
    return turn_id


def test_record_turn_fires_alert_when_signals_present():
    db = _FakeDB()
    mock_alert = AsyncMock(return_value={"sent": True})
    with patch("cto_services.db.get_db", return_value=db), \
         patch("services.founder_alerts.send_founder_alert", mock_alert):
        turn_id = asyncio.run(_run_and_drain(
            user_id="u1", project_id="p1", tools_called=["read_repo_file"],
            citation_guard_triggered=False, citation_guard_paths_fetched=[],
            citation_guard_unverified=[], system_signals_emitted=["github_auth_failed"],
            llm_model="glm-5.2", response_tokens=100, was_retry=False,
        ))
    assert turn_id is not None
    mock_alert.assert_called_once()
    kwargs = mock_alert.call_args.kwargs
    assert kwargs["source_key"] == "ora_signal:github_auth_failed"
    assert kwargs["level"] == "warning"
    assert kwargs["guard"] == "ora_signal"
    assert "u1" in kwargs["detail"]


def test_record_turn_does_not_fire_alert_when_no_signals():
    db = _FakeDB()
    mock_alert = AsyncMock(return_value={"sent": True})
    with patch("cto_services.db.get_db", return_value=db), \
         patch("services.founder_alerts.send_founder_alert", mock_alert):
        asyncio.run(_run_and_drain(
            user_id="u1", project_id="p1", tools_called=[],
            citation_guard_triggered=False, citation_guard_paths_fetched=[],
            citation_guard_unverified=[], system_signals_emitted=[],
            llm_model="glm-5.2", response_tokens=10, was_retry=False,
        ))
    mock_alert.assert_not_called()


def test_record_turn_dedupes_duplicate_signal_names_within_one_turn():
    """A turn can emit the same signal twice (e.g. two failed GitHub
    calls) — only one alert per unique signal name, not one per
    occurrence (send_founder_alert's own 6h dedup handles repeats
    ACROSS turns, but within a single turn we should never double-
    fire for the exact same signal name)."""
    db = _FakeDB()
    mock_alert = AsyncMock(return_value={"sent": True})
    with patch("cto_services.db.get_db", return_value=db), \
         patch("services.founder_alerts.send_founder_alert", mock_alert):
        asyncio.run(_run_and_drain(
            user_id="u1", project_id="p1", tools_called=[],
            citation_guard_triggered=False, citation_guard_paths_fetched=[],
            citation_guard_unverified=[],
            system_signals_emitted=["github_auth_failed", "github_auth_failed"],
            llm_model="glm-5.2", response_tokens=10, was_retry=False,
        ))
    assert mock_alert.call_count == 1


def test_record_turn_fires_one_alert_per_distinct_signal_type():
    db = _FakeDB()
    mock_alert = AsyncMock(return_value={"sent": True})
    with patch("cto_services.db.get_db", return_value=db), \
         patch("services.founder_alerts.send_founder_alert", mock_alert):
        asyncio.run(_run_and_drain(
            user_id="u1", project_id="p1", tools_called=[],
            citation_guard_triggered=False, citation_guard_paths_fetched=[],
            citation_guard_unverified=[],
            system_signals_emitted=["github_auth_failed", "repo_not_found"],
            llm_model="glm-5.2", response_tokens=10, was_retry=False,
        ))
    assert mock_alert.call_count == 2
    keys = {c.kwargs["source_key"] for c in mock_alert.call_args_list}
    assert keys == {"ora_signal:github_auth_failed", "ora_signal:repo_not_found"}


def test_record_turn_returns_immediately_even_if_alert_send_is_slow():
    """The alert must be fire-and-forget (asyncio.create_task) —
    record_turn itself must not block on send_founder_alert."""
    db = _FakeDB()

    async def _slow_alert(*a, **kw):
        await asyncio.sleep(5)  # would fail the test's own timeout if awaited inline
        return {"sent": True}

    with patch("cto_services.db.get_db", return_value=db), \
         patch("services.founder_alerts.send_founder_alert", _slow_alert):
        turn_id = asyncio.run(asyncio.wait_for(
            audit_log.record_turn(
                user_id="u1", project_id="p1", tools_called=[],
                citation_guard_triggered=False, citation_guard_paths_fetched=[],
                citation_guard_unverified=[], system_signals_emitted=["github_auth_failed"],
                llm_model="glm-5.2", response_tokens=10, was_retry=False,
            ),
            timeout=1.0,
        ))
    assert turn_id is not None
