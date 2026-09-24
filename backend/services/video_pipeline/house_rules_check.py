"""
services/video_pipeline/house_rules_check.py — 2026-09-10, enforces
the admin's video-generation house rules against a script before it
reaches TTS/lip-sync.

Reuses the app's existing OpenRouter → Claude path (services/llm) —
this is an internal call to already-integrated infrastructure (the
same one routers/admin_health_score.py and call_emergent_watchdog
already lean on), not a new third-party integration.

Fails OPEN, not closed: if the LLM call itself errors (timeout, bad
JSON, provider outage), the script passes through unchanged rather
than blocking the founder's own video because of an unrelated LLM
hiccup — the intentional violation-blocking behavior only fires on an
actual parsed "this violates the rules" verdict.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from services.llm.openrouter_client import call_openrouter_model
from services.llm.openrouter_providers import _CLAUDE_MODEL

logger = logging.getLogger("video_pipeline.house_rules_check")


class Verdict:
    def __init__(self, blocked: bool, reason: Optional[str], final_script: str):
        self.blocked = blocked
        self.reason = reason
        self.final_script = final_script


async def evaluate(script: str, rules_text: str, mode: str) -> Verdict:
    rules_text = (rules_text or "").strip()
    if not rules_text:
        return Verdict(blocked=False, reason=None, final_script=script)

    if mode == "rewrite":
        system = (
            "You rewrite short video-narration scripts so they comply with a "
            "founder's house rules, while preserving the original meaning, "
            "tone, and roughly the same length. Reply with ONLY the rewritten "
            "script text — no preamble, no quotes, no explanation."
        )
        user = f"House rules:\n{rules_text}\n\nOriginal script:\n{script}"
        try:
            revised = await call_openrouter_model(
                model=_CLAUDE_MODEL, system=system, user=user,
                max_tokens=600, temperature=0.3)
            revised = (revised or "").strip()
            if not revised:
                return Verdict(blocked=False, reason=None, final_script=script)
            return Verdict(blocked=False, reason=None, final_script=revised)
        except Exception as e:                                      # noqa: BLE001
            logger.warning("house rules rewrite failed, using original script: %r", e)
            return Verdict(blocked=False, reason=None, final_script=script)

    # mode == "block"
    system = (
        "You check a short video-narration script against a founder's house "
        "rules. Reply with ONLY a JSON object, no other text: "
        '{"violates": true|false, "reason": "<one short sentence, or empty '
        'string if it does not violate>"}'
    )
    user = f"House rules:\n{rules_text}\n\nScript to check:\n{script}"
    try:
        raw = await call_openrouter_model(
            model=_CLAUDE_MODEL, system=system, user=user,
            max_tokens=200, temperature=0.0)
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start:end + 1])
        if parsed.get("violates"):
            reason = (parsed.get("reason") or "Violates the video house rules.").strip()
            return Verdict(blocked=True, reason=reason, final_script=script)
        return Verdict(blocked=False, reason=None, final_script=script)
    except Exception as e:                                          # noqa: BLE001
        logger.warning("house rules check failed, allowing script through: %r", e)
        return Verdict(blocked=False, reason=None, final_script=script)
