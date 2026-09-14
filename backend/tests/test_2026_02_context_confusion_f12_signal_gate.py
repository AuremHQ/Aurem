"""
tests/test_2026_02_context_confusion_f12_signal_gate.py - Part B of
the architectural "Context Confusion" fix (2026-02).

Root cause (confirmed by reading services/chat_helpers.classify_intent
before this fix): a captured F12 browser-error payload alone was
sufficient to force Mode D (the debugging pipeline) onto ANY message,
completely independent of what the user's own message actually said.
Combined with ChatPanel.jsx attaching that payload on every send()
(including retries), a stale or unrelated browser error could hijack
an otherwise unrelated turn into an off-topic debug reply about
`js:317` instead of answering (or retrying) the user's real request.

This exercises the exact founder-reported reproduction case: a
footer-text-update request, with a real (non-noise) F12 payload
attached, must NOT route to Mode D - because the message itself
never references an error.
"""
from services.chat_helpers import classify_intent, references_environment_signal


REAL_F12_PAYLOAD = {
    "console_errors": [{"message": "TypeError: Cannot read properties of undefined (reading 'map') at js:317"}],
    "network_errors": [],
}
NOISE_F12_PAYLOAD = {"console_errors": [{"message": "aborted"}], "network_errors": []}


def test_unrelated_task_with_real_f12_signal_does_not_hijack_into_mode_d():
    # The founder's exact live reproduction: a footer-text-update
    # request, with a genuinely real (non-noise) captured browser
    # error sitting in the F12 buffer from something unrelated.
    msg = "update the footer text to say 'contact us today'"
    assert classify_intent(msg, REAL_F12_PAYLOAD) != "D"


def test_retry_of_the_same_unrelated_task_also_does_not_hijack():
    # The Retry-button variant of the same bug.
    msg = "update the footer text to say 'contact us today'"
    assert classify_intent(msg, REAL_F12_PAYLOAD) != "D"
    # Simulate a second call (the retry) with the identical stale
    # payload still attached - must be equally unaffected.
    assert classify_intent(msg, REAL_F12_PAYLOAD) != "D"


def test_a_message_that_actually_references_the_error_still_routes_to_mode_d():
    for msg in (
        "check that error for me", "what's wrong with the app right now?",
        "why is this broken", "the console is full of errors, can you look?",
    ):
        assert classify_intent(msg, REAL_F12_PAYLOAD) == "D", msg


def test_noise_only_payload_never_routes_to_mode_d_even_if_message_mentions_error():
    # Unchanged prior behavior: a noise-only F12 buffer must still be
    # filtered out by _f12_has_real_signal regardless of the message.
    assert classify_intent("what's wrong?", NOISE_F12_PAYLOAD) != "D"


def test_no_payload_at_all_is_unaffected_by_this_fix():
    assert classify_intent("update the footer text", None) != "D"
    assert classify_intent("debug this crash for me", None) == "D"


def test_references_environment_signal_matches_the_tickets_own_examples():
    assert references_environment_signal("check that error") is True
    assert references_environment_signal("what's wrong?") is True
    assert references_environment_signal("update the footer text") is False
