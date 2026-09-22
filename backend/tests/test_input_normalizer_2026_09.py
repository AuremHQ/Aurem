"""
tests/test_input_normalizer_2026_09.py — Section 15.1 spec.

Backend input normalizer: the real guarantee (frontend punctuation
guard can always be bypassed by a different client).
"""
from __future__ import annotations

import pytest

from services.input_normalizer import (
    GOLDEN_ROUND_TRIP_SET,
    check_golden_set,
    normalize_input,
)


@pytest.mark.parametrize("original,expected,desc", GOLDEN_ROUND_TRIP_SET)
def test_golden_round_trip(original, expected, desc):
    result = normalize_input(original)
    assert result["text"] == expected, f"{desc}: got {result['text']!r}"


def test_golden_set_helper_reports_zero_failures():
    report = check_golden_set()
    assert report["failures"] == [], f"golden set regressed: {report['failures']}"


def test_idempotent_running_twice_equals_running_once():
    tricky = "she said \u201chello\u2014world\u2019s wait\u2026 fine\u201d"
    once = normalize_input(tricky)["text"]
    twice = normalize_input(once)["text"]
    assert once == twice


def test_already_ascii_message_is_a_true_no_op():
    text = "npm run build -- --force and git commit -m 'x'"
    result = normalize_input(text)
    assert result["text"] == text
    assert result["changed"] is False


def test_zero_width_space_stripped_but_real_newline_and_tab_kept():
    text = "line1\u200b\nindented:\ttab\u200bhere"
    result = normalize_input(text)
    assert "\u200b" not in result["text"]
    assert "\n" in result["text"]
    assert "\t" in result["text"]


def test_massively_altered_short_message_is_flagged_not_silently_applied():
    """Safety valve: if NFKC's OWN incremental contribution (beyond
    what the deliberate reversal map + zero-width strip alone would
    do) would change >5% of the message, skip NFKC and flag for
    review — per spec 15.1 ('never silently drop content; if
    normalization would change more than ~5%... don't auto-apply').
    Fullwidth Unicode forms are a real, legitimate NFKC compatibility
    decomposition target NOT covered by the typographic reversal map
    — a short message made almost entirely of them is exactly the
    kind of 'something unusual is happening' case the valve exists
    for (adversarial/unexpected input, not ordinary punctuation)."""
    text = "\uff41\uff42\uff43\uff44\uff45"  # fullwidth "abcde"
    result = normalize_input(text)
    assert result["text"] == text, "must return ORIGINAL text unchanged when NFKC's own risk is too high"
    assert result["flagged"] is True
    assert result["changed"] is False
    assert result["change_fraction"] > 0.05


def test_heavy_control_char_garbage_still_gets_stripped_unconditionally():
    """Unlike NFKC, zero-width/control stripping is a deliberate,
    always-safe step (same as the reversal map) — it must apply
    regardless of what fraction of the message it removes."""
    text = "\x01\x02\x03\x04\x05\x06\x07\x08x"  # 8 control chars + 1 real char
    result = normalize_input(text)
    assert result["text"] == "x"
    assert result["changed"] is True
    assert result["flagged"] is False


def test_heavy_typographic_substitution_in_a_short_message_still_applies():
    """A short message that's almost ENTIRELY deliberate typographic
    chars (dashes/quotes) must still apply cleanly — the reversal map
    is always-safe by design, not gated by the risk fraction (a
    couple of curly quotes in an ordinary short sentence is common
    and must never get blocked)."""
    text = "\u2014\u2014\u2014"  # 3 em-dashes
    result = normalize_input(text)
    assert result["text"] == "------"
    assert result["changed"] is True
    assert result["flagged"] is False


def test_emoji_and_rtl_text_never_touched():
    text = "\U0001F600 \u0631\u062d\u0628\u0627 emoji and Arabic stay exactly as-is"
    result = normalize_input(text)
    assert result["text"] == text
    assert result["changed"] is False


def test_normalize_input_never_raises_on_empty_or_none():
    assert normalize_input("")["text"] == ""
    assert normalize_input(None)["text"] is None
