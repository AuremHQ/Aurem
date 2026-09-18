"""
Bug 8 fix (2026-09-17) — 33.9% task success rate with no failure
breakdown. Verifies the text-based reason classifier and that
`_set_status()` fills in `error_code` for gate-rejected failures
(which never raise an exception, so the exception-classifier path
never runs for them) without overwriting an already-set code.
"""
import pytest
from unittest.mock import AsyncMock, patch

from services.failure_taxonomy import classify_failure_reason_from_text, FailureReason


def test_classifies_hallucination_gate_message():
    msg = ("AI kept producing content that does not match the "
           "real file (refusing to push):\n  - foo")
    assert classify_failure_reason_from_text(msg) == FailureReason.HALLUCINATION_GATE


def test_classifies_design_lint_block():
    msg = "Design linter blocked commit:\nhardcoded color #fff"
    assert classify_failure_reason_from_text(msg) == FailureReason.POLICY_BLOCKED


def test_classifies_timeout():
    msg = "Model API was slow to respond — waited 48s and only got 0 tool calls"
    assert classify_failure_reason_from_text(msg) == FailureReason.TIMEOUT


def test_classifies_context_not_found():
    msg = "I don't see that in your connected website"
    assert classify_failure_reason_from_text(msg) == FailureReason.CONTEXT_NOT_FOUND


def test_classifies_user_cancelled():
    assert classify_failure_reason_from_text("task cancelled by user") == FailureReason.USER_CANCELLED


def test_unrecognized_message_is_unknown():
    assert classify_failure_reason_from_text("some random crash") == FailureReason.UNKNOWN


@pytest.mark.asyncio
async def test_set_status_fills_missing_error_code(monkeypatch):
    from services import cto_projects_helpers as helpers

    captured = {}
    class _FakeTasks:
        async def update_one(self, q, update):
            captured.update(update["$set"])

    class _FakeDB:
        cto_tasks = _FakeTasks()

    monkeypatch.setattr("cto_services.db.get_db", lambda: _FakeDB())
    with patch("services.error_translator.translate",
               AsyncMock(return_value={"plain": "x", "steps": [], "suggestion": "", "source": "llm"})):
        await helpers._set_status(
            "t1", status="failed",
            error="Design linter blocked commit:\nbad color",
        )
    assert captured["error_code"] == FailureReason.POLICY_BLOCKED


@pytest.mark.asyncio
async def test_set_status_never_overwrites_an_explicit_error_code(monkeypatch):
    from services import cto_projects_helpers as helpers

    captured = {}
    class _FakeTasks:
        async def update_one(self, q, update):
            captured.update(update["$set"])

    class _FakeDB:
        cto_tasks = _FakeTasks()

    monkeypatch.setattr("cto_services.db.get_db", lambda: _FakeDB())
    await helpers._set_status(
        "t1", status="failed",
        error="some internal bug",
        error_code="INTERNAL_CALL_ERROR",
    )
    assert captured["error_code"] == "INTERNAL_CALL_ERROR"
