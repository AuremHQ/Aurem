"""
core/parliament/self_heal.py — 2026-09-08 Phase 3 god-class split.

`SelfHeal`. Moved verbatim out of the single core/parliament.py
(zero logic change).
"""
from __future__ import annotations


from . import llm_call as _llm_call_mod
from .breaker import _GLOBAL_BREAKER
from .scoring import _strip_fences


class SelfHeal:
    """Healer used by Verify phase to recover linter failures.

    Critical contract: this class **never** adds its own retry counter
    on top of the caller's.  The caller (loop_engine._do_verify)
    passes `round_num` and `max_rounds`; this class enforces only the
    contract supplied.  Without this, loop_engine's existing 2-round
    loop + a parliament internal round = 4 rounds total which is
    undefined behaviour."""

    SYS_PROMPT = (
        "You are ORA in self-heal mode.  A file you wrote failed static "
        "analysis.  Rewrite ONLY the file content to fix the reported "
        "errors.  Do not add commentary.  Do not wrap in code fences.  "
        "Preserve all existing functionality that wasn't responsible "
        "for the failure."
    )

    # 2026-11-25 audit Part IX (e): whole-file heals. A file over
    # this size is NOT silently truncated — heal() escalates instead
    # of deleting code the model never saw. 60_000 chars ≈ 15k input
    # tokens; the output cap scales with the file (below).
    WHOLE_FILE_MAX_CHARS = 60_000
    # Tripwire: a healed file that lost over half its length is
    # treated as data loss, not a fix — escalate, don't ship it.
    MIN_HEALED_FRACTION = 0.5

    async def heal(self, *, task: str, all_attempts: list[dict],
                   round_num: int,
                   max_rounds: int = 2,
                   review_mode: str | None = None) -> dict:
        """Heal a file based on the failing attempts so far.

        `all_attempts` is a list of `{output, score, error}` dicts —
        the most recent is the current broken state.  `round_num` is
        the heal-round counter (caller-owned).  `max_rounds` is the
        caller's hard ceiling.  This class respects it; no internal
        retry is added.

        `review_mode` (2026-11-25 audit Part IX (e)): the LLM tier
        for the heal — "swift" (cheap) / "pro" / "maxx". When None,
        round 1 heals use "swift" (a syntax fix does not need the
        Pro model) and later rounds escalate to "pro". The old code
        hardcoded "pro" for every heal, including trivial first-round
        syntax fixes.

        Returns: {status, output, round_num, max_rounds, temp_used,
                  reason}.  Status is `"retry"` (caller may re-verify),
                  `"escalate"` (caller's max reached or LLM gave up),
                  or `"circuit_open"` (breaker tripped — caller should
                  use its legacy fallback path).
        """
        if round_num >= max_rounds:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "caller max rounds reached",
            }
        if not all_attempts:
            return {
                "status":     "retry",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "no attempts supplied",
            }
        # Circuit breaker — bail out cheaply if upstream LLM is sick.
        if not _GLOBAL_BREAKER.should_attempt():
            return {
                "status":     "circuit_open",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "global circuit breaker is OPEN",
            }

        last = all_attempts[-1]
        # 2026-11-25 audit Part IX (b): .get() returns None when the
        # key is PRESENT with value None — `None[:6000]` raised
        # TypeError. `or ""` (the idiom already used at L84) guards
        # both lines.
        last_output = last.get("output") or ""
        last_error = last.get("error") or ""
        # 2026-11-25 audit Part IX (a): NEVER truncate the file the
        # model must rewrite. The old `[:6000]` + max_tokens=2500
        # meant the caller replaced the WHOLE file with a rewrite of
        # only its first 6,000 chars — code past char 6000 was
        # permanently deleted. Files up to WHOLE_FILE_MAX_CHARS are
        # sent in full with an output cap scaled to cover a full
        # rewrite; larger files escalate (fail-closed) instead of
        # being truncated.
        if len(last_output) > self.WHOLE_FILE_MAX_CHARS:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     ("file_too_large_to_heal_safely: "
                               f"{len(last_output)} chars > "
                               f"{self.WHOLE_FILE_MAX_CHARS} whole-file cap"),
            }
        history_block = ""
        if len(all_attempts) > 1:
            history_block = "\n\n--- PRIOR FAILED ATTEMPTS ---\n"
            for i, prev in enumerate(all_attempts[:-1], start=1):
                err = (prev.get("error") or "")[:240]
                history_block += f"Attempt {i} error: {err}\n"
            history_block += "Do NOT repeat the same fix.\n"
        # Temperature escalation per round — bounded.
        temp = min(0.05 + 0.15 * round_num, 0.35)
        # Output cap must cover a COMPLETE rewrite of the input:
        # ~4 chars/token, plus headroom. (The old flat 2500 forced
        # the truncation the input cap was hiding.)
        max_tokens = min(16_000, max(2500, len(last_output) // 2 + 500))
        # 2026-11-25 audit Part IX (c): first-round heals use the
        # cheap tier; escalate to "pro" only on later rounds (or when
        # the caller explicitly asks for a tier).
        effective_mode = review_mode or ("pro" if round_num >= 1 else "swift")
        user_msg = (
            task + history_block + (
                f"\n\n--- CURRENT CONTENT (COMPLETE FILE, "
                f"{len(last_output)} chars — return it ALL, do not omit "
                f"any part, even unchanged sections) ---\n{last_output}\n"
                f"--- END CONTENT ---\n\n"
                f"--- LAST ERROR ---\n{last_error[:1000]}\n"
                f"--- END ERROR ---\n\nReturn the corrected file content only."
            )
        )
        content, _ms, err = await _llm_call_mod._llm_call_protected(
            system=self.SYS_PROMPT, user=user_msg,
            max_tokens=max_tokens, mode="code", review_mode=effective_mode,
            temperature=temp,
            trace_name="parliament.selfheal",
            trace_metadata={
                "round_num":   round_num,
                "max_rounds":  max_rounds,
                "temp":        temp,
            },
        )
        if err:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  temp,
                "reason":     f"llm:{err}",
            }
        out = _strip_fences(content)
        if not out:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  temp,
                "reason":     "empty_output",
            }
        # 2026-11-25 audit Part IX (a) tripwire: a rewrite that lost
        # over half the file is data loss, not a fix — escalate with
        # the ORIGINAL intact (the caller keeps it) instead of
        # shipping the shortened file.
        if len(last_output) > 1000 and len(out) < len(last_output) * self.MIN_HEALED_FRACTION:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  temp,
                "reason":     ("healed_output_much_shorter_than_original: "
                               f"{len(out)} chars vs {len(last_output)} original"),
            }
        return {
            "status":     "retry",
            "output":     out,
            "round_num":  round_num,
            "max_rounds": max_rounds,
            "temp_used":  temp,
            "reason":     None,
        }
