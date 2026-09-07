"""
routers/upload.py — Convert uploaded files to LLM-readable form.

Two paths:

1. **Document path** (PDF / DOCX / XLSX / PPTX / HTML / CSV / text) →
   Microsoft's MarkItDown converts to clean Markdown so the chat LLM
   can read it without burning tokens on raw binary noise.

2. **Image path** (PNG / JPG / WEBP / GIF / BMP / screenshots) →
   MarkItDown returns nothing useful for images without OCR set up.
   Instead we route the image to an OpenRouter vision model (Gemini
   Flash 1.5) which both *describes* the image AND OCRs any visible
   text. The result comes back as Markdown the chat LLM can act on.

POST /api/aurem-dev/upload/convert
  multipart-form-data file=<binary>
  → {ok, filename, content_type, original_size, markdown, md_size,
     kind: "doc" | "image"}
"""
# arch: allow-http — R2/S3 object upload (iter 212m-225)
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from cto_services.auth import current_dev

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["Upload / MarkItDown"])

# Hard cap to protect the server from huge uploads.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 25 MB
# Markdown output cap so we don't blow the LLM context window.
MAX_MD_CHARS = 60_000

# What we consider an image — handled by the vision path, NOT MarkItDown.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp",
                "image/gif", "image/bmp"}

# Vision model — Gemini Flash 1.5 via OpenRouter is cheap, fast, and
# strong at both image description AND OCR. We pin a specific revision
# so a silent upstream rename can't surprise us.
#
# Iter 110 — production was returning "I cannot see the screenshot"
# because OpenRouter's gemini-2.5-flash-lite intermittently returns
# 400 "Unable to process input image" for some user screenshots.
# We now try a chain: configured model → gpt-4o-mini (verified working).
# Either one returning content is success.
# Vision describer lives in services/vision_describe.py (2026-09-07 extraction);
# `_describe_image_via_vision` stays importable here for existing callers/tests.
from services.vision_describe import (  # noqa: E402
    describe_image_via_vision as _describe_image_via_vision,
    _PRIMARY_VISION_MODEL, _FALLBACK_VISION_MODEL, _VISION_PROMPT,  # noqa: F401
)


@router.post("/convert")
async def upload_convert(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Read an uploaded file, convert it to Markdown, and return the
    markdown body to the frontend so it can be appended to the chat
    prompt as clean LLM-readable text."""
    # Auth required so we don't expose this as an open conversion endpoint
    user = await current_dev(authorization)
    # Iter 364 · Phase 3 — token hard-stop for image-branch (uses a
    # vision LLM). Text/PDF conversions are stdlib-cheap so they
    # aren't gated, but IMAGE_EXTS hits `_describe_image_via_vision`
    # which is a real LLM call we need to protect.
    from services.usage import assert_has_budget
    await assert_has_budget(user["user_id"])

    raw = await file.read()
    size = len(raw)
    if size == 0:
        raise HTTPException(400, "Empty upload")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"File too large ({size // (1024 * 1024)}MB). "
            f"Max is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )

    suffix = Path(file.filename or "").suffix.lower() or ""
    ctype = (file.content_type or "").lower()

    # ── Image branch — bypass MarkItDown, use vision LLM ───────────────
    if suffix in IMAGE_EXTS or ctype in IMAGE_MIMES:
        description = await _describe_image_via_vision(
            raw, ctype, file.filename or "image",
        )
        if not description:
            # Vision call failed AND we have no other text to fall back
            # on. Still return success with a placeholder so the
            # frontend's pill shows the attachment instead of going
            # blank. The chat LLM at least sees "user attached an image"
            # rather than nothing.
            description = (
                "_(The user attached an image but vision OCR is "
                "unavailable right now. Ask them to paste any visible "
                "text or describe what they see in the image.)_"
            )
        text = description.strip()
        if len(text) > MAX_MD_CHARS:
            text = text[:MAX_MD_CHARS] + "\n\n... [truncated by server cap]"
        return {
            "ok":            True,
            "kind":          "image",
            "filename":      file.filename or "image",
            "content_type":  ctype,
            "original_size": size,
            "md_size":       len(text),
            "truncated":     False,
            "markdown":      text,
        }

    # ── Document branch — MarkItDown ───────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tf:
        tf.write(raw)
        tf.flush()
        try:
            # Import inside the handler so a missing optional dependency
            # in a stripped-down deploy doesn't break router import.
            from markitdown import MarkItDown
            md = MarkItDown()
            result = md.convert(tf.name)
        except ImportError:
            logger.exception("markitdown not installed")
            raise HTTPException(
                500, "MarkItDown library not installed on server",
            )
        except Exception as e:
            logger.exception(
                "markitdown convert failed for %r", file.filename,
            )
            raise HTTPException(415, f"Couldn't convert this file: {e}")

    text = (getattr(result, "text_content", None) or "").strip()
    if not text:
        # No usable text. Don't 415 — return a clear placeholder so the
        # frontend pill still shows the attachment and the chat LLM at
        # least knows something was sent. Better than silent failure.
        text = (
            f"_(The user uploaded **{file.filename or 'a file'}** but "
            f"the server couldn't extract any readable text from it. "
            f"Ask them what they wanted you to do with it.)_"
        )

    truncated = False
    if len(text) > MAX_MD_CHARS:
        text = text[:MAX_MD_CHARS] + "\n\n... [truncated by server cap]"
        truncated = True

    return {
        "ok":            True,
        "kind":          "doc",
        "filename":      file.filename or "upload",
        "content_type":  ctype,
        "original_size": size,
        "md_size":       len(text),
        "truncated":     truncated,
        "markdown":      text,
    }
