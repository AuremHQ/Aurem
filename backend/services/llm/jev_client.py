"""
services/llm/jev_client.py — TypeSafe Jev (typesafe/jev-1.13) via
OpenRouter's Decisions API. Added 2026-09-19.

Jev is NOT a chat-completion model — it doesn't share the
`/api/v1/chat/completions` shape or pricing model that
`openrouter_client.py` wraps. It's a typed-decision model: you send
`state` (the situation) + `questions` (a small typed schema —
noul/choice/score) and get back typed answers with probabilities
attached. No free-text output, no JSON-parsing-the-LLM's-prose layer
needed afterward. Endpoint: POST /api/alpha/decisions (note: "alpha"
— OpenRouter's own versioning for this API surface, independent of
the `-1.13` model version).

Use this ONLY for classification/routing/triage/confidence-scoring
decisions — never for anything that generates code, PR descriptions,
or written explanations. Those stay on the normal chat models via
`openrouter_client.py`.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Literal, Optional

import httpx

logger = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"

QuestionType = Literal["noul", "choice", "score"]


class JevError(Exception):
    """Raised on a non-2xx response or malformed answer — callers
    should catch this and fall back to the regular chat-model path,
    same as any other soft-fail LLM call in this codebase."""


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise JevError("OPENROUTER_API_KEY not set")
    return key


async def ask_decisions(
    *,
    state: Any,
    questions: dict[str, dict[str, Any]],
    model: str = DEFAULT_MODEL,
    timeout: float = 5.0,
) -> dict:
    """Low-level call to the Decisions API. `questions` maps a caller
    -chosen key -> {"type": "noul"|"choice"|"score", "instructions": str,
    "criteria": {...}}. Returns the raw parsed JSON response
    ({"answers": {...}, "usage": {...}, ...}).

    Raises JevError on any failure — never returns a partial/garbage
    answer silently. Callers are expected to treat this the same as
    any other LLM call that can fail: catch, log, fall back.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                DECISIONS_URL,
                headers={
                    "Authorization": f"Bearer {_api_key()}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "state": state, "questions": questions},
            )
    except httpx.HTTPError as e:
        raise JevError(f"decisions_request_failed: {e!r}") from e
    if r.status_code != 200:
        raise JevError(f"decisions_status_{r.status_code}: {r.text[:200]}")
    try:
        data = r.json()
    except Exception as e:                                # noqa: BLE001
        raise JevError(f"decisions_bad_json: {e!r}") from e
    if "answers" not in data:
        raise JevError(f"decisions_missing_answers: {data!r}"[:300])
    return data


async def ask_choice(
    *,
    state: Any,
    instructions: str,
    criteria: dict[str, str],
    model: str = DEFAULT_MODEL,
    timeout: float = 5.0,
) -> tuple[Optional[str], dict]:
    """Convenience wrapper for the common "pick exactly one label"
    case (what `intent_router.py`'s LLM layer, or any fix/dismiss/ask
    triage, needs). Returns (label_or_None, meta) — meta always has
    `usage` and `probabilities` when the call succeeded, and `error`
    when it didn't, mirroring the (raw, usage, err) shape used
    elsewhere in this codebase's classify_* helpers so call sites can
    plug this in without changing their own error-handling shape.
    """
    try:
        data = await ask_decisions(
            state=state,
            questions={"answer": {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria,
            }},
            model=model,
            timeout=timeout,
        )
    except JevError as e:
        return None, {"error": str(e)[:200], "model": model}
    ans = (data.get("answers") or {}).get("answer") or {}
    label = ans.get("choice")
    if label not in criteria:
        return None, {"error": f"unexpected_label:{label!r}", "model": model,
                       "usage": data.get("usage", {})}
    return label, {
        "model": data.get("model", model),
        "probabilities": ans.get("probabilities", {}),
        "usage": data.get("usage", {}),
    }
