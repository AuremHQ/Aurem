"""Iter 212m-14 — Runtime hallucination guard for status claims.

The persona's ANTI-HALLUCINATION CONTRACT already forbids fabricated
file paths, line numbers, and metrics. But the founder hit a class
the prompt didn't cover: **claims about resource HEALTH/STATUS**
without any tool evidence. Specifically ORA said

    [FIX] The GitHub PAT appears unauthorized — would need a fresh
    fine-grained token with `Contents: Read` access.

…even though the PAT in question was valid (verified independently
via `/cto/projects/{id}/check-pat`) AND no tool call this turn had
returned a 401. The LLM fabricated the diagnosis.

This module post-processes the final assistant content. When it
detects a credential/auth status claim with NO corresponding tool
error in the turn's invocation history, it REMOVES the unsupported
sentence and appends a transparency footer so the founder is
signalled to verify. Part XII (a3) changed the original
footer-only design (which left the hallucinated claim in the reply
verbatim, presented as fact with only a footnote): a visible footer
next to a false claim still lets the claim reach the user as the
answer. The claim is now stripped at the guard layer and replaced
with an explicit marker; the footer no longer quotes the claim text.
Rewriting is confined to the flagged sentence only — handoff fences
/ code blocks elsewhere in the reply are untouched.

False-positive strategy:
  • Match ONLY narrow status-of-credential claims (PAT, token,
    auth, permission denied, 401/403). Vague phrases like
    "you might need a token" are NOT flagged — they're correct
    hedging.
  • If the sentence already hedges with might/may/could/perhaps/
    let-me-check/verify AND the hedge governs the claim itself
    (appears before / inside the claim, not after it as a hedge on
    some other clause), treat it as honest uncertainty and skip.
  • Tool-call output containing 401/403/unauthorized/forbidden/
    bad-credentials counts as evidence — even if just one matching
    invocation, the claim is considered supported.

This guard is intentionally surgical. We do NOT broadly rewrite
LLM outputs; we only annotate one specific high-trust failure
mode that bit a real user in production.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Narrow patterns — claims about credentials/auth being bad.
_CRED_CLAIMS = [
    # Part XII (a1): `unauthoriz` only — the old `(?:un)?authoriz`
    # matched BOTH "authorized" and "unauthorized", so a TRUE
    # statement ("The PAT is authorized") was flagged as an
    # unsupported status claim.
    re.compile(r"\bPAT\s+(?:is|appears|seems|looks|might\s+be|may\s+be|got)\s+unauthoriz(?:ed|ation)?", re.I),
    re.compile(r"\bPAT\s+(?:is|appears|seems|looks|got)\s+(?:expir|invalid|miss|broken|wrong|bad|insufficient)", re.I),
    re.compile(r"\b(?:GitHub\s+)?token\s+(?:is|appears|seems|looks|got)\s+(?:expir|invalid|miss|broken|wrong|bad|unauthor)", re.I),
    re.compile(r"\bunauthorize(?:d)?\b\s+(?:to|on|access|request)", re.I),
    re.compile(r"\bappears?\s+unauthorize", re.I),
    re.compile(r"\bpermission\s+denied\b", re.I),
    re.compile(r"\b401\s+unauthoriz", re.I),
    re.compile(r"\b403\s+forbidden", re.I),
    re.compile(r"\bcredentials?\s+(?:are|appear|seem|look)\s+(?:invalid|expir|bad|wrong|miss|insufficient)", re.I),
    re.compile(r"\binsufficient\s+(?:permissions?|scope|access)", re.I),
    re.compile(r"\bbad\s+credentials\b", re.I),
]


# Hedged language — sentences containing any of these get a pass.
# Honest uncertainty is fine; we only flag DEFINITIVE-sounding claims.
_HEDGES = re.compile(
    r"\b(?:might|maybe|may|could|perhaps|possibly|likely|i\s+haven't|"
    r"haven't\s+verified|haven't\s+checked|let\s+me\s+check|"
    r"let\s+me\s+verify|need\s+to\s+verify|will\s+verify|"
    r"would\s+(?:need|like)\s+to\s+(?:check|verify|confirm))",
    re.I,
)


# Tool errors that DO count as evidence for an auth claim.
_AUTH_FAILURE = re.compile(
    r"401|403|unauthoriz|forbidden|bad[_\s-]?credential|permission[_\s-]?denied|"
    r"invalid[_\s-]?token|expir(?:ed|ation)|insufficient[_\s-]?(?:scope|permission)|"
    r"no[_\s-]?pat|pat[_\s-]?missing|missing[_\s-]?pat",
    re.I,
)


def _has_supporting_tool_evidence(invocations: list[dict]) -> bool:
    """Did any tool call this turn surface a real auth failure?

    A failure counts if either:
      • ok=False AND error/output text matches _AUTH_FAILURE, OR
      • the tool output JSON literally contains 401/403/unauthorized
        strings (some tools return `ok=True` with an embedded error).
    """
    for inv in invocations or []:
        # Explicit failure
        if inv.get("ok") is False:
            err = str(inv.get("error") or "")
            if _AUTH_FAILURE.search(err):
                return True
        # Output payload — even successful invocations sometimes embed
        # diagnostic strings (e.g. check_pat returns state:"expired").
        out = inv.get("output")
        if out:
            text = str(out)[:4000]
            if _AUTH_FAILURE.search(text):
                return True
    return False


def _detect_unsupported_claims(content: str) -> list[str]:
    """Return the trimmed text of every line/sentence that contains
    a credential/auth status claim AND lacks a hedge GOVERNING that
    claim.

    Part XII (a2): the old code skipped a sentence if a hedge word
    appeared ANYWHERE in it — "The PAT is unauthorized, it may need
    a fresh token" was exempted even though "may" hedges the remedy,
    not the status claim (and `\\bmay\\b` also matched the month
    "May"). Now a hedge only exempts a claim when it starts before
    the claim match ends — i.e. it precedes or sits inside the claim
    itself ("The PAT may be unauthorized" stays unflagged; a hedge
    after the claim does not)."""
    flagged: list[str] = []
    if not content:
        return flagged
    # Split on sentence-ish boundaries (. ! ? \n). Keep it cheap.
    sentences = re.split(r"(?<=[.!?\n])\s+", content)
    for s in sentences:
        s_norm = s.strip()
        if not s_norm:
            continue
        claim_matches = [m for pat in _CRED_CLAIMS if (m := pat.search(s_norm))]
        if not claim_matches:
            continue
        hedge_starts = [hm.start() for hm in _HEDGES.finditer(s_norm)]
        # A claim is hedged only if a hedge starts before the claim
        # match ends. Flag the sentence if ANY claim in it is unhedged.
        unhedged = [m for m in claim_matches
                    if not any(h < m.end() for h in hedge_starts)]
        if unhedged:
            flagged.append(s_norm[:240])
    return flagged


_REMOVED_CLAIM_MARKER = "[Unverified status claim removed — I did not verify this.]"


def _strip_flagged_sentences(content: str, flagged: list[str]) -> str:
    """Remove each flagged sentence from `content`, replacing it with
    `_REMOVED_CLAIM_MARKER` so the reply never presents the
    hallucinated claim as verified. Exact-match replace first; a
    flagged entry truncated at 240 chars falls back to a prefix
    search on the sentence split."""
    out = content
    for f in flagged:
        if f and f in out:
            out = out.replace(f, _REMOVED_CLAIM_MARKER, 1)
            continue
        # Truncated entry — find the sentence it came from.
        if f:
            parts = re.split(r"(?<=[.!?\n])\s+", out)
            for i, p in enumerate(parts):
                if p.strip()[:240] == f:
                    parts[i] = _REMOVED_CLAIM_MARKER
                    out = " ".join(parts)
                    break
    return out


def apply(content: str, invocations: list[dict]) -> tuple[str, list[str]]:
    """Public entry point.

    Returns `(new_content, flagged_claims)`. When `flagged_claims`
    is non-empty AND there's no supporting tool evidence, the flagged
    sentence(s) are REMOVED from the returned content (replaced with
    an explicit marker) and a transparency footer is appended. The
    footer does NOT quote the claim text — quoting it would put the
    hallucinated claim back in front of the user. Otherwise content
    is unchanged.

    Never raises — guard failures must never break the chat reply.

    Caller note (Part XII a3): `apply()` returns the reply; it does
    not itself send anything. Callers already send the returned
    content, so no caller change is needed for this fix — but any
    caller that ignores the return value and sends the original
    `content` would bypass the strip entirely (combiner: verify
    call sites send `apply()`'s return value).
    """
    try:
        if not content or not isinstance(content, str):
            return content, []
        flagged = _detect_unsupported_claims(content)
        if not flagged:
            return content, []
        if _has_supporting_tool_evidence(invocations):
            # The model's claim is grounded in a real tool error —
            # nothing to annotate, the [ISSUE]/[FIX] marker is honest.
            return content, []

        # Strip the hallucinated claim(s) — they must not reach the
        # user presented as verified. Keep the footer as the audit
        # trail, without re-quoting the claim itself.
        stripped = _strip_flagged_sentences(content, flagged)
        suffix = (
            "\n\n---\n"
            "[ISSUE] Self-check: I removed an unverified status claim "
            "above — I made it without evidence from a tool call this "
            "turn. If it matters, ask me to run `check_pat` / "
            "`read_repo_file` and re-confirm.\n"
        )
        return stripped + suffix, flagged
    except Exception as e:                                    # noqa: BLE001
        logger.warning("hallucination_guard.apply crashed: %r", e)
        return content, []


__all__ = ["apply"]
