"""
services/advisor_vision.py — Iter 212m-212

Isolated OpenRouter vision call for the Advisor `/screenshot` slash
command.

Explicitly ISOLATED from:
  - the Council chain (advisor's main LLM path in `chat.py`)
  - the tool orchestrator (`chat_with_tools`, `services/llm.py`)
  - the retry / fallback ladder in `services/llm_router.py`

Rationale: if OpenRouter is degraded, the Gemini quota is exhausted,
or the model returns garbage, the Advisor's *normal* text reply must
keep working.  Same isolation pattern the Suggestion Box uses for
its Groq call — a bounded, single-shot, best-effort call that
returns `None` on any failure and never raises.

Contract
========

    analyze_screenshot(png_bytes, user_prompt) -> str | None

        `str`  — a concise UI description ready to inline into the
                 Advisor's context block.
        `None` — best-effort fetch failed; caller must degrade
                 gracefully.  Never raises.

Model selection
---------------

Primary (chosen 2026-02 after `manual_ab_model_swap.py` vision-lane
A/B):
    google/gemini-2.5-flash

Failover (if primary 5xx / rate-limited):
    openai/gpt-5-mini

Both are vision-capable via OpenRouter, priced at ~$0.30/M input
and ~$2.50/M output.  Failover is intentionally single-hop — no
deep ladder — to keep this call bounded (~10s wall-time cap).
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import httpx

from services.http import ext_request
from services.http.client import ExternalCallError

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

PRIMARY_MODEL = os.environ.get("ADVISOR_VISION_MODEL",
                                "google/gemini-2.5-flash")
FAILOVER_MODEL = os.environ.get("ADVISOR_VISION_FAILOVER",
                                 "openai/gpt-5-mini")

# System prompt is deliberately short — vision-input tokens dominate
# cost, and we want a *description* not a novel.
SYSTEM_PROMPT = (
    "You are describing a UI screenshot to another assistant.  The "
    "user is looking at this screen and just asked a question about "
    "it.  In ≤140 words, describe what you SEE with SPATIAL "
    "SPECIFICITY — say `top-right`, `top-left`, `bottom nav`, "
    "`sidebar`, `middle of the card`, etc.  Name buttons by their "
    "visible label and colour ('orange Start Free button', 'dark "
    "Continue with GitHub button').  Call out any obvious defect "
    "(overflow, misalignment, unreadable text, broken image, error "
    "banner) and where it sits.  Do NOT invent content that isn't "
    "visible.  Do NOT speculate about backend causes.  If the "
    "screenshot is blank / a login wall / an error screen, say so "
    "in one sentence and stop.  End with one line: "
    "`PROBABLE_ISSUES:` followed by a comma-separated list, or "
    "`PROBABLE_ISSUES: none`."
)

_TIMEOUT_S = 12.0
_MAX_USER_HINT_CHARS = 400


_CHARS_PER_TOKEN_ESTIMATE = 4  # rough, provider-agnostic fallback only


async def _log_advisor_vision_usage(model: str, usage: dict, *,
                                    content_chars: int = 0,
                                    status: str = "ok",
                                    error_reason: Optional[str] = None) -> None:
    """Closing-the-bypass-doors audit (2026-10, Part 1.2) — this raw
    OpenRouter call had zero usage logging. Falls back to a character-
    length estimate (marked `estimated=True`) when no real `usage`
    object comes back. Never raises; a ledger failure must never
    break the Advisor screenshot reply it's describing."""
    try:
        from services.loop_token_ledger import log_real_usage
        has_real_tokens = bool(usage) and (
            usage.get("input_tokens") or usage.get("output_tokens")
        )
        estimated = False
        if has_real_tokens:
            clean_usage = dict(usage)
        elif status == "ok" and content_chars > 0:
            estimated = True
            clean_usage = {
                "output_tokens": max(1, content_chars // _CHARS_PER_TOKEN_ESTIMATE),
            }
        else:
            clean_usage = {}
        await log_real_usage(
            model, clean_usage, feature="Advisor Vision", provider="openrouter",
            status=status, error_reason=error_reason, estimated=estimated,
        )
    except Exception as e:                                    # noqa: BLE001
        logger.debug("advisor_vision: usage ledger write skipped: %r", e)


async def _call_openrouter(
    model: str,
    png_bytes: bytes,
    user_hint: str,
) -> Optional[str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        logger.warning("advisor_vision: OPENROUTER_API_KEY missing")
        return None

    # OpenRouter accepts data URIs directly for image content.
    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{b64}"

    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"User question: {user_hint[:_MAX_USER_HINT_CHARS]}"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # OpenRouter attribution headers — nice-to-have, not required.
        "HTTP-Referer": "https://auremcto.com",
        "X-Title": "AUREM - Advisor Vision",
    }
    try:
        r = await ext_request(
            "openrouter", "POST", OPENROUTER_URL,
            json=payload, headers=headers,
            timeout=httpx.Timeout(
                connect=5.0, read=_TIMEOUT_S, write=10.0, pool=5.0,
            ),
            raise_for_status=False,
        )
    except ExternalCallError as e:
        logger.warning("advisor_vision: transport error on %s: %r", model, e)
        return None
    except Exception as e:
        logger.warning("advisor_vision: transport error on %s: %r", model, e)
        return None

    if r.status_code != 200:
        logger.warning(
            "advisor_vision: %s returned %s — body: %s",
            model, r.status_code, r.text[:200],
        )
        await _log_advisor_vision_usage(model, {}, status="failed",
                                        error_reason=f"http_{r.status_code}")
        return None
    try:
        j = r.json()
        content = (
            j.get("choices") or [{}]
        )[0].get("message", {}).get("content")
    except Exception as e:
        logger.warning("advisor_vision: bad JSON from %s: %r", model, e)
        await _log_advisor_vision_usage(model, {}, status="failed",
                                        error_reason=f"{type(e).__name__}: {e}")
        return None
    if not content or not isinstance(content, str):
        await _log_advisor_vision_usage(model, {}, status="failed",
                                        error_reason="empty_content")
        return None

    u = j.get("usage") or {}
    await _log_advisor_vision_usage(model, {
        "input_tokens": int(u.get("prompt_tokens", 0) or 0),
        "output_tokens": int(u.get("completion_tokens", 0) or 0),
        "cost": u.get("cost"),
    }, content_chars=len(content))
    return content.strip()


async def analyze_screenshot(
    png_bytes: bytes,
    user_prompt: str,
) -> Optional[str]:
    """Best-effort vision analysis.  Returns None on ANY failure so
    the caller can degrade gracefully."""
    if not png_bytes:
        return None
    if len(png_bytes) < 1024:
        # Anything under 1 KB is almost certainly a 1x1 pixel or
        # decode failure — skip the round-trip.
        logger.warning(
            "advisor_vision: rejecting tiny screenshot (%d bytes)",
            len(png_bytes),
        )
        return None

    reply = await _call_openrouter(PRIMARY_MODEL, png_bytes, user_prompt)
    if reply:
        return reply
    # Failover — intentionally single-hop so this call can't sprawl
    # into a long ladder that blocks the advisor request.
    reply = await _call_openrouter(FAILOVER_MODEL, png_bytes, user_prompt)
    return reply  # may still be None; caller handles that
