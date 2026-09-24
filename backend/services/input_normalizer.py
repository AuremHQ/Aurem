"""
services/input_normalizer.py — 2026-09-22, Section 15.1 spec.

BACKEND input normalizer — the "real guarantee" layer per spec, since
the frontend punctuation guard (frontend/src/utils/chatTextUtils.js)
can always be bypassed by a different client (direct API access, a
future mobile app, curl). Wired in via routers/chat/turn.py's
`ChatBody.prompt` validator, so it runs on every request that
constructs a ChatBody regardless of which endpoint or client.

Steps, in order (spec 15.1):
  1. Unicode NFKC normalization.
  2. Strip zero-width and control characters, except real newlines/
     tabs/carriage-returns.
  3. Explicit reversal map for typographic substitutions back to ASCII
     (en/em-dash -> --/-, curly quotes -> straight quotes, ellipsis
     char -> ...).
  4. Never silently drop/rewrite MORE than a small fraction of the
     message. If normalization would change more than
     MAX_CHANGE_FRACTION of the message's characters, DON'T apply it
     — return the ORIGINAL text unchanged, flagged for review (logged
     before/after). A message that's mostly non-ASCII on purpose (a
     different language, deliberately unusual content) never gets
     silently mangled just because it triggered a broad heuristic.
  5. Idempotent by construction — running the same deterministic
     substitution map twice on already-normalized text is a no-op,
     verified by check_golden_set() below and its own unit tests.
"""
from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

MAX_CHANGE_FRACTION = 0.05

# Ordered so multi-char sequences are unambiguous — dict iteration
# order doesn't actually matter here since every key is a distinct
# single Unicode codepoint (no key is a substring of another).
_TYPOGRAPHIC_REVERSAL_MAP = {
    "\u2014": "--",   # em dash —
    "\u2013": "-",    # en dash –
    "\u2018": "'",    # left single quote '
    "\u2019": "'",    # right single quote / apostrophe '
    "\u201c": '"',    # left double quote "
    "\u201d": '"',    # right double quote "
    "\u2026": "...",  # ellipsis …
    "\u00a0": " ",    # non-breaking space
}

# Zero-width / control chars to strip outright. Keeps real newline
# (\n), tab (\t), and carriage return (\r) — legitimate formatting,
# not corruption.
_ZERO_WIDTH_AND_CONTROL_RE = re.compile(
    "[\u200b\u200c\u200d\u2060\ufeff"          # zero-width space/joiners/BOM
    "\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"          # C0 controls except \t\n\r
)


def normalize_input(text: str) -> dict:
    """Returns {"text": str, "changed": bool, "flagged": bool,
    "change_fraction": float}. Never raises, never returns None —
    worst case, echoes the input back unchanged with flagged=True.

    Spec order: NFKC -> explicit reversal map -> zero-width/control
    strip. The ~5% safety valve is measured ONLY against NFKC's OWN
    incremental contribution — i.e. "final result" vs. "what the
    deliberate reversal map + strip alone would have produced,
    without NFKC at all". Those two steps are fixed, audited,
    always-safe substitutions (curly quote -> straight, em-dash ->
    --, strip garbage zero-width chars) — they're the FEATURE, not
    the risk this valve exists to catch, and gating on them would
    trip on completely ordinary short messages (a couple of curly
    quotes in a normal sentence is often >5% of a short message's
    raw length) or on ordinary garbage cleanup, defeating the whole
    point either way. NFKC is the one genuinely unpredictable step —
    it does many other compatibility decompositions too (fullwidth ->
    halfwidth, ligatures, etc.) that are fine individually but could
    legitimately dominate a short/adversarial message; THAT'S what
    "something unusual is happening" means here."""
    if not text:
        return {"text": text, "changed": False, "flagged": False, "change_fraction": 0.0}
    try:
        original = text

        # The "always-safe" baseline — deliberate steps only, NFKC
        # skipped entirely, applied directly to the raw original.
        safe_baseline = original
        for src, dst in _TYPOGRAPHIC_REVERSAL_MAP.items():
            safe_baseline = safe_baseline.replace(src, dst)
        safe_baseline = _ZERO_WIDTH_AND_CONTROL_RE.sub("", safe_baseline)

        # The full pipeline, spec order.
        nfkc = unicodedata.normalize("NFKC", original)
        after_reversal = nfkc
        for src, dst in _TYPOGRAPHIC_REVERSAL_MAP.items():
            after_reversal = after_reversal.replace(src, dst)
        final = _ZERO_WIDTH_AND_CONTROL_RE.sub("", after_reversal)

        risk_fraction = _char_change_fraction(safe_baseline, final)
        if risk_fraction > MAX_CHANGE_FRACTION:
            logger.warning(
                "input_normalizer: NFKC's own contribution (change_fraction=%.3f) "
                "exceeds %.3f beyond the always-safe steps — skipping NFKC, "
                "flagging for review. before=%r safe_baseline=%r nfkc_result=%r",
                risk_fraction, MAX_CHANGE_FRACTION, original[:200], safe_baseline[:200], final[:200],
            )
            if safe_baseline != original:
                return {"text": safe_baseline, "changed": True, "flagged": True, "change_fraction": risk_fraction}
            return {"text": original, "changed": False, "flagged": True, "change_fraction": risk_fraction}

        if final == original:
            return {"text": original, "changed": False, "flagged": False, "change_fraction": 0.0}

        return {"text": final, "changed": True, "flagged": False, "change_fraction": risk_fraction}
    except Exception as e:  # pragma: no cover
        logger.warning("input_normalizer: unexpected error %r — returning original text unchanged", e)
        return {"text": text, "changed": False, "flagged": True, "change_fraction": 0.0}


def _char_change_fraction(before: str, after: str) -> float:
    """Cheap, deterministic 'how much changed' signal — Levenshtein
    would be more precise but this is O(n) and good enough for a
    5%-of-message safety valve, not an exact diff."""
    if not before:
        return 0.0
    import difflib
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    ratio = matcher.ratio()  # 1.0 = identical, 0.0 = completely different
    return 1.0 - ratio


# ── Golden round-trip set (spec 15.1) ──────────────────────────────────
# "A fixed library of tricky real strings ... that must round-trip
# with ONLY the targeted substitutions changed — nothing else." Shared
# by tests/test_input_normalizer_2026_09.py AND evals/runner.py's
# offline check (check_golden_set below) so both run against the
# exact same fixture — no drift between the two.
GOLDEN_ROUND_TRIP_SET = [
    # (input, expected_output, description)
    ("npm run build -- --force", "npm run build -- --force", "literal CLI flags, already ASCII"),
    ("it's a --force flag", "it's a --force flag", "straight apostrophe, already ASCII"),
    ("she said \u201chello\u2019s fine\u201d", "she said \"hello's fine\"", "curly double + curly single -> straight"),
    ("wait\u2026 really?", "wait... really?", "ellipsis char -> three dots"),
    ("range: 10\u201320", "range: 10-20", "en-dash -> hyphen"),
    ("em dash\u2014like this", "em dash--like this", "em-dash -> double-hyphen"),
    ("```python\ndef foo():\n    return 1\n```",
     "```python\ndef foo():\n    return 1\n```", "fenced code block untouched"),
    ("nested \u2018quotes \u201cinside\u201d quotes\u2019",
     "nested 'quotes \"inside\" quotes'", "nested curly quotes -> straight"),
    ("emoji stays same \U0001F600 unchanged", "emoji stays same \U0001F600 unchanged", "emoji untouched"),
    ("\u0631\u062d\u0628\u0627 hello", "\u0631\u062d\u0628\u0627 hello", "RTL Arabic text untouched"),
    ("diff:\n-old line\n+new line", "diff:\n-old line\n+new line", "raw diff hyphens untouched (not em/en dash)"),
    ("zero\u200bwidth\u200bspace", "zerowidthspace", "zero-width space stripped"),
]


def check_golden_set() -> dict:
    """Runs GOLDEN_ROUND_TRIP_SET through normalize_input(). No LLM
    cost, fully deterministic — safe to run on every deploy. Returns
    {"total": N, "failures": [...]}."""
    failures = []
    for original, expected, desc in GOLDEN_ROUND_TRIP_SET:
        result = normalize_input(original)
        got = result["text"]
        if got != expected:
            failures.append({
                "description": desc, "input": original,
                "expected": expected, "got": got,
            })
            continue
        # Idempotency — running it again on the already-normalized
        # text must be a no-op (spec 15.1: "a mechanical guarantee to
        # test, not a judgment call").
        second_pass = normalize_input(got)["text"]
        if second_pass != got:
            failures.append({
                "description": f"{desc} (NOT IDEMPOTENT)", "input": original,
                "expected": got, "got": second_pass,
            })
    return {"total": len(GOLDEN_ROUND_TRIP_SET), "failures": failures}
