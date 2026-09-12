"""
services/visual_bugfix_vision.py — multimodal vision analysis for the
Visual Bug-Fix pipeline (Phase 2). One Gemini 3.1 Pro vision call per
run (see the Cost Guard in the founder's spec: max 1 vision call + 1
code-gen call per run — code-gen is a later phase, not built yet).

Model: gemini-3.1-pro-preview via emergentintegrations (EMERGENT_LLM_KEY)
— founder-approved (2026-09-12) for best OCR + code-correlation balance
with zero extra setup.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid

logger = logging.getLogger(__name__)

_VISION_MODEL = "gemini-3.1-pro-preview"

_SYSTEM_PROMPT = (
    "You are a meticulous UI bug triager. You are shown a screenshot of "
    "a web app with a visible visual defect, plus the user's own "
    "description of what's wrong. Your job is ONLY to observe and "
    "extract — you do NOT write code or suggest a fix.\n\n"
    "Respond with a SINGLE JSON object, no prose before or after, no "
    "markdown code fences, matching EXACTLY this shape:\n"
    "{\n"
    '  "visual_defects": [<string>, ...],       // short phrases, e.g. "misaligned flex container", "text truncated"\n'
    '  "extracted_strings": [<string>, ...],     // EXACT visible text/labels/button copy/error messages in the image, verbatim\n'
    '  "inferred_dom_elements": [<string>, ...], // best-guess CSS-selector-ish hints, e.g. "button.primary-btn", "div.modal-container"\n'
    '  "suggested_patterns": [<string>, ...]     // broader search terms for a code search, e.g. "*Modal*", "*Button*", a component-ish name\n'
    "}\n\n"
    "Rules:\n"
    "  - extracted_strings must be VERBATIM text visible in the image — "
    "no paraphrasing, no invented text.\n"
    "  - If you cannot make out a field confidently, return an empty "
    "array for it rather than guessing wildly.\n"
    "  - Keep every array to at most 8 items.\n"
)


def _extract_json_object(raw: str) -> dict | None:
    """Best-effort JSON extraction from an LLM response that may be
    wrapped in prose or markdown fences."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _clean_list(v, cap: int = 8) -> list[str]:
    if not isinstance(v, list):
        return []
    out = [str(x).strip() for x in v if str(x).strip()]
    return out[:cap]


async def analyze_screenshot(image_bytes: bytes, mime_type: str,
                              bug_description: str) -> dict:
    """One vision call. Returns:
      {ok, visual_defects, extracted_strings, inferred_dom_elements,
       suggested_patterns, raw_response, error?}
    Never raises — a failed/malformed call returns ok=False with an
    `error` string so the caller can surface a clean message instead
    of a 500.
    """
    key = (os.environ.get("EMERGENT_LLM_KEY") or "").strip()
    if not key:
        return {"ok": False, "error": "EMERGENT_LLM_KEY not set",
                "visual_defects": [], "extracted_strings": [],
                "inferred_dom_elements": [], "suggested_patterns": [],
                "raw_response": None}

    from emergentintegrations.llm.chat import (
        LlmChat, UserMessage, FileContentWithMimeType,
    )

    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type, "png")
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=True) as tf:
        tf.write(image_bytes)
        tf.flush()
        chat = (
            LlmChat(
                api_key=key,
                session_id=f"visual-bugfix-{uuid.uuid4().hex[:8]}",
                system_message=_SYSTEM_PROMPT,
            )
            .with_model("gemini", _VISION_MODEL)
        )
        prompt = (
            f"User's description of the bug: {bug_description or '(none given)'}\n\n"
            "Analyze the attached screenshot and return the JSON now."
        )
        msg = UserMessage(
            text=prompt,
            file_contents=[FileContentWithMimeType(mime_type=mime_type, file_path=tf.name)],
        )
        try:
            raw = await chat.send_message(msg)
        except Exception as e:                                # noqa: BLE001
            logger.warning("[visual_bugfix_vision] LLM call failed: %r", e)
            return {"ok": False, "error": f"vision call failed: {type(e).__name__}: {e}",
                    "visual_defects": [], "extracted_strings": [],
                    "inferred_dom_elements": [], "suggested_patterns": [],
                    "raw_response": None}

    parsed = _extract_json_object(raw)
    if parsed is None:
        return {"ok": False, "error": "model returned no parseable JSON",
                "visual_defects": [], "extracted_strings": [],
                "inferred_dom_elements": [], "suggested_patterns": [],
                "raw_response": raw}

    return {
        "ok": True,
        "visual_defects":        _clean_list(parsed.get("visual_defects")),
        "extracted_strings":     _clean_list(parsed.get("extracted_strings")),
        "inferred_dom_elements": _clean_list(parsed.get("inferred_dom_elements")),
        "suggested_patterns":    _clean_list(parsed.get("suggested_patterns")),
        "raw_response":          raw,
    }
