"""
Regression iter413 (2026-11-26) — the mock provider sentinel leaked
as "ORA" on /api/aurem-dev/chat/send.

MASTER TASK Part A1 (2026-09-28) applied sanitize_provider_for_user()
to _mock_send_response(); the sanitizer collapsed EVERY non-empty raw
value to "ORA", so a mock turn's provider read "ORA" instead of "mock"
— while _mock_send_response's own docstring still promised
provider='mock'. Two pre-existing tests pinned the sentinel and both
broke: tests/test_interaction_reliability_contract.py::
test_quoted_no_edit_phrase_is_negative_control_not_conflict
(assert 'ORA' == 'mock', live job 140073510664 run #738) and
tests/test_mock_short_circuit_chat_send_2026_09_08.py::
test_mock_short_circuit_chat_send.

Fix: the sanitizer passes the "mock" sentinel through unchanged — it
is a mode marker, not a vendor/model identity. The frontend still
brands it "ORA" for display (brandProvider("mock") is "ORA"), so what
a user sees is unchanged; every real vendor/model codename still
collapses to "ORA".
"""


def test_regression_iter413_mock_provider_sentinel_survives_sanitizer():
    from services.user_response_sanitizer import sanitize_provider_for_user

    # The sentinel: a mode marker, not a vendor identity.
    assert sanitize_provider_for_user("mock") == "mock"
    # Real vendor/model codenames still collapse to the ORA brand.
    for vendor in (
        "anthropic/claude-sonnet-4.5",
        "gpt-5.2",
        "gemini-3-pro",
        "qwen3:8b",
        "deepseek-chat",
    ):
        assert sanitize_provider_for_user(vendor) == "ORA"
    # Empty stays empty.
    assert sanitize_provider_for_user("") == ""
    assert sanitize_provider_for_user(None) == ""


def test_regression_iter413_mock_send_response_provider_is_mock():
    from routers.chat.turn import MOCK_SEND_TEXT, ChatBody, _mock_send_response

    body = ChatBody(prompt="hello", session_id="s_iter413")
    out = _mock_send_response(body, {"user_id": "u_iter413"})
    assert out["provider"] == "mock"
    assert out["content"] == MOCK_SEND_TEXT
    assert out["meta"] == {"mock": True}
