"""
services/vision_describe.py — image → Markdown description via a vision
LLM (OpenRouter primary + fallback). Extracted verbatim from
routers/upload.py (2026-09-07) so services/browser_tools.py stops
importing a router (architecture boundary: service-imports-router).
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import os

import httpx

from services.http import ext_client

logger = logging.getLogger(__name__)


_PRIMARY_VISION_MODEL = os.getenv("AUREM_VISION_MODEL", "google/gemini-2.5-flash-lite")
_FALLBACK_VISION_MODEL = os.getenv("AUREM_VISION_FALLBACK_MODEL", "openai/gpt-4o-mini")
_VISION_PROMPT = (
    "You are part of an AI coding assistant. The user just attached an "
    "image to their chat. Produce a structured Markdown response that "
    "the downstream model can act on. Sections:\n\n"
    "**Visual description** — what's in the image (UI screenshot, "
    "diagram, photo, chart, error stack). 2-3 sentences.\n\n"
    "**Extracted text** — verbatim text visible in the image, "
    "preserving layout / code formatting / error messages. If it's a "
    "code screenshot, fence the code with ```. If it's an error, "
    "include the full stack trace. If there's no text, write "
    "\"(no text in image)\".\n\n"
    "**Likely intent** — one line: what is the user probably asking the "
    "AI to do with this image?\n\n"
    "Be exhaustive on Extracted text — OCR every visible word. Do not "
    "summarise away error messages or stack traces; the user needs them "
    "verbatim to debug."
)


async def describe_image_via_vision(raw: bytes, content_type: str,
                                     filename: str) -> str:
    """Send the image (as a base64 data URL) to a vision LLM and return
    its Markdown description. Returns an empty string on any failure so
    the caller can decide whether to surface a degraded response or
    bail with 415."""
    api_key = (
        os.getenv("OPENROUTER_API_KEY")
        or os.getenv("EMERGENT_LLM_KEY")
        or ""
    ).strip()
    if not api_key:
        logger.warning("vision OCR skipped — no OPENROUTER_API_KEY set")
        return ""

    mime = content_type or mimetypes.guess_type(filename)[0] or "image/png"
    b64 = base64.b64encode(raw).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    async def _call(model: str) -> str:
        payload = {
            "model": model,
            "max_tokens": 1200,
            "temperature": 0.2,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": data_url}},
                ],
            }],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://auremcto.com",
            "X-Title": "AUREM - Vision Describe",
        }
        async with ext_client("openrouter", timeout=httpx.Timeout(45.0)) as c:
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=payload,
            )
            if r.status_code != 200:
                logger.warning("vision call HTTP %s on %s — body: %s",
                                r.status_code, model, r.text[:300])
                return ""
            data = r.json()
            return (data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content") or "").strip()

    # Iter 110 — primary → fallback chain. First model that returns
    # non-empty content wins. The fallback model (gpt-4o-mini) was
    # picked because it's vision-capable, cheap, and we verified live
    # that it succeeds on images where gemini-2.5-flash-lite returns 400.
    try:
        out = await _call(_PRIMARY_VISION_MODEL)
        if out:
            return out
        logger.info("vision primary (%s) empty — trying fallback (%s)",
                    _PRIMARY_VISION_MODEL, _FALLBACK_VISION_MODEL)
        return await _call(_FALLBACK_VISION_MODEL)
    except Exception as e:
        logger.exception("vision call failed: %r", e)
        # Last-chance try the fallback in case the exception was on
        # the primary call's transport layer (e.g. rare httpx timeout).
        try:
            return await _call(_FALLBACK_VISION_MODEL)
        except Exception:
            return ""
