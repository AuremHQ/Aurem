"""
services/llm/jev_client.py — TypeSafe Jev via Emergent's Universal LLM
Key proxy. Added 2026-09-19, rewired 2026-02 to the correct transport.

2026-02 fix: this originally called OpenRouter's Decisions API
directly (https://openrouter.ai/api/alpha/decisions, model
typesafe/jev-1.13) using a raw OPENROUTER_API_KEY from .env. That key
was dead (OpenRouter returned 401 "User not found" on every call),
silently degrading every caller (semantic_mode_router.py,
intent_router.py) to their keyword-fallback path instead of real Jev
classification. Per the integration playbook, Jev runs ONLY on the
Emergent universal key (EMERGENT_LLM_KEY) through Emergent's own
proxy (INTEGRATION_PROXY_URL, defaulting to
https://integrations.emergentagent.com) — there is no own-key/
OpenRouter option. Now uses the `typesafe-sdk` client against that
proxy instead.

Jev is NOT a chat-completion model — it doesn't share the
`/api/v1/chat/completions` shape or pricing model that
`openrouter_client.py` wraps. It's a typed-decision model: you send
`state` (the situation) + `questions` (a small typed schema —
noul/choice/score) and get back typed answers with probabilities
attached. No free-text output, no JSON-parsing-the-LLM's-prose layer
needed afterward.

Use this ONLY for classification/routing/triage/confidence-scoring
decisions — never for anything that generates code, PR descriptions,
or written explanations. Those stay on the normal chat models via
`openrouter_client.py`.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Literal, Optional

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    Noul,
    RetryPolicy,
    Score,
    TypeSafeAPIError,
    TypeSafeError,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "jev-latest"

QuestionType = Literal["noul", "choice", "score"]


class JevError(Exception):
    """Raised on a non-2xx response or malformed answer — callers
    should catch this and fall back to the regular chat-model path,
    same as any other soft-fail LLM call in this codebase."""


@lru_cache(maxsize=1)
def _client() -> AsyncTypeSafeClient:
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        raise JevError("EMERGENT_LLM_KEY not set")
    proxy = (
        os.getenv("INTEGRATION_PROXY_URL")
        or os.getenv("integration_proxy_url")
        or "https://integrations.emergentagent.com"
    ).rstrip("/")
    return AsyncTypeSafeClient(
        api_key=key,
        base_url=f"{proxy}/llm/typesafe",
        retry=RetryPolicy(max_retries=2, respect_retry_after=False),
    )


def _build_question(q: dict[str, Any]):
    """Map our plain-dict question shape ({"type", "instructions",
    "criteria"}) onto the SDK's typed builders."""
    qtype = q.get("type")
    instructions = q.get("instructions") or ""
    if qtype == "choice":
        return Choice(instructions=instructions, criteria=q.get("criteria") or {})
    if qtype == "score":
        return Score(instructions=instructions, criteria=q.get("criteria") or [])
    if qtype == "noul":
        return Noul(instructions=instructions)
    raise JevError(f"unknown_question_type:{qtype!r}")


async def ask_decisions(
    *,
    state: Any,
    questions: dict[str, dict[str, Any]],
    model: str = DEFAULT_MODEL,
    timeout: float = 5.0,
) -> dict:
    """Low-level call to the Decisions API. `questions` maps a caller
    -chosen key -> {"type": "noul"|"choice"|"score", "instructions": str,
    "criteria": {...}}. Returns a plain dict shaped like the proxy's
    own wire format ({"answers": {...}, "usage": {...}}) so callers
    written against the old raw-HTTP response keep working unchanged.

    Raises JevError on any failure — never returns a partial/garbage
    answer silently. Callers are expected to treat this the same as
    any other LLM call that can fail: catch, log, fall back.
    """
    try:
        built_questions = {k: _build_question(v) for k, v in (questions or {}).items()}
        resp = await _client().system_one(
            state=state, questions=built_questions, model=model, timeout=timeout,
        )
    except JevError:
        raise
    except TypeSafeAPIError as e:
        raise JevError(f"decisions_status_{e.status}: {str(e)[:200]}") from e
    except TypeSafeError as e:
        raise JevError(f"decisions_request_failed: {e!r}") from e
    except Exception as e:                                # noqa: BLE001
        raise JevError(f"decisions_unexpected: {e!r}") from e

    answers: dict[str, Any] = {}
    for key, ans in (resp.answers or {}).items():
        if hasattr(ans, "choice"):
            answers[key] = {"type": "choice", "choice": ans.choice,
                             "probabilities": dict(ans.probabilities or {})}
        elif hasattr(ans, "score"):
            answers[key] = {"type": "score", "score": ans.score,
                             "legend": {str(k): v for k, v in (ans.legend or {}).items()},
                             "probabilities": {str(k): v for k, v in (ans.probabilities or {}).items()}}
        elif hasattr(ans, "noul"):
            answers[key] = {"type": "noul", "noul": ans.noul}
    usage = dict(getattr(resp, "usage", None) or {})
    return {"model": getattr(resp, "model", model), "answers": answers, "usage": usage}


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
