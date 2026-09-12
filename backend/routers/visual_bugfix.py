"""
routers/visual_bugfix.py — Multimodal Visual Bug-Fix pipeline,
Phase 1 (upload/storage) + Phase 2 (vision analysis + codebase
grounding). Phase 3 (patch generation + headless preview) and Phase 4
(before/after comparison UI) are explicitly NOT built yet — founder
approved a phased rollout (2026-09-12), Phase 1+2 first.

Flow:
  1. POST /upload           — client sends the raw screenshot file.
     We validate it, persist it via Emergent Object Storage, and
     record a `visual_bug_reports` doc (14-day TTL — see
     services/visual_bugfix_storage.py's module docstring for exactly
     what that TTL does and doesn't cover).
  2. GET  /{id}/image        — serves the stored bytes back (used for
     the composer thumbnail AND the eventual before/after card).
  3. POST /{id}/analyze      — ONE Gemini vision call (Cost Guard: max
     1 vision + 1 code-gen call per run) extracting visual_defects /
     extracted_strings / inferred_dom_elements / suggested_patterns,
     then correlates those against the user's CONNECTED project repo
     using the existing `search_repo` (grep, deterministic — matches
     the founder's "Deterministic Localization" requirement) and
     `semantic_search_repo` (GitHub Code Search + local TF-IDF
     fallback) tools from services/local_tools.py — reused as-is, no
     new indexing system built.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from PIL import Image

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.bin_context import build_bin_context
from services.usage import assert_has_task_budget
from services.visual_bugfix_storage import get_object, put_object
from services.visual_bugfix_vision import analyze_screenshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/visual-bugfix", tags=["visual-bugfix"])

_ALLOWED_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MAX_BYTES = 10 * 1024 * 1024
_TTL_SECONDS = 14 * 24 * 3600


async def _ensure_ttl_index(db) -> None:
    """Create a TTL index on `visual_bug_reports.created_at` (idempotent,
    mirrors routers/scaffold.py's `_ensure_ttl_index`). This is the ONLY
    real "auto-expire" mechanism here — see visual_bugfix_storage.py's
    module docstring for why the backing object-storage bytes
    themselves can't be deleted."""
    try:
        await db.visual_bug_reports.create_index(
            "created_at", expireAfterSeconds=_TTL_SECONDS, name="visual_bug_reports_ttl",
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] TTL index create failed: %r", e)


async def _resolve_user(authorization: Optional[str]) -> dict:
    payload = await current_dev(authorization)
    if not payload or not payload.get("user_id"):
        raise HTTPException(401, "Invalid token")
    db = get_db()
    user = await db.dev_users.find_one({"user_id": payload["user_id"]})
    if not user:
        raise HTTPException(401, "User not found")
    return user


async def _get_owned_report(report_id: str, user_id: str) -> dict:
    db = get_db()
    doc = await db.visual_bug_reports.find_one({"report_id": report_id})
    if not doc:
        raise HTTPException(404, "Report not found")
    if doc.get("user_id") != user_id:
        raise HTTPException(404, "Report not found")
    return doc


@router.post("/upload")
async def upload_screenshot(
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    user = await _resolve_user(authorization)
    await assert_has_task_budget(user["user_id"])   # Pre-Flight Quota Check — before ANY vision $ is spent
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported image type: {content_type or 'unknown'} "
                                  "(png, jpeg, webp only)")
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(400, f"Image too large ({len(data)} bytes, max {_MAX_BYTES})")
    if not data:
        raise HTTPException(400, "Empty file")

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except Exception:
        raise HTTPException(400, "Could not read image — file may be corrupt")

    file_hash = hashlib.sha256(data).hexdigest()
    report_id = uuid.uuid4().hex
    ext = _ALLOWED_TYPES[content_type]
    storage_path = f"visual-bugfix/{user['user_id']}/{report_id}.{ext}"

    try:
        await put_object(storage_path, data, content_type)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] storage upload failed: %r", e)
        raise HTTPException(502, "Could not store image — please try again")

    now = datetime.now(timezone.utc)
    db = get_db()
    await _ensure_ttl_index(db)
    await db.visual_bug_reports.insert_one({
        "report_id":     report_id,
        "user_id":       user["user_id"],
        "project_id":    project_id,
        "storage_path":  storage_path,
        "content_type":  content_type,
        "file_hash":     file_hash,
        "width":         width,
        "height":        height,
        "status":        "uploaded",
        "vision_analysis": None,
        "candidate_files": [],
        "created_at":    now,
    })
    return {
        "ok": True,
        "id": report_id,
        "image_url": f"/api/aurem-dev/visual-bugfix/{report_id}/image",
        "width": width,
        "height": height,
        "file_hash": file_hash,
    }


@router.get("/{report_id}/image")
async def get_screenshot(
    report_id: str,
    authorization: Optional[str] = Header(None),
    auth: Optional[str] = Query(None),
    variant: str = Query("before", pattern="^(before|after)$"),
):
    # `<img src>` can't set custom headers — accept the token as a
    # query param too, same convention as routers/github_app.py's
    # install-redirect route.
    user = await _resolve_user(authorization or (f"Bearer {auth}" if auth else None))
    doc = await _get_owned_report(report_id, user["user_id"])
    storage_path = doc.get("storage_path") if variant == "before" else doc.get("after_storage_path")
    if not storage_path:
        raise HTTPException(404, "No preview image yet for this report")
    try:
        data, content_type = await get_object(storage_path)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] storage fetch failed: %r", e)
        raise HTTPException(502, "Could not load image")
    return Response(content=data, media_type=content_type or doc.get("content_type"))


@router.post("/{report_id}/analyze")
async def analyze_screenshot_endpoint(
    report_id: str,
    body: dict,
    authorization: Optional[str] = Header(None),
):
    user = await _resolve_user(authorization)
    doc = await _get_owned_report(report_id, user["user_id"])
    bug_description = (body or {}).get("bug_description") or ""
    project_id = (body or {}).get("project_id") or doc.get("project_id")

    db = get_db()
    await db.visual_bug_reports.update_one(
        {"report_id": report_id}, {"$set": {"status": "analyzing"}},
    )

    try:
        image_bytes, content_type = await get_object(doc["storage_path"])
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] storage fetch failed pre-analysis: %r", e)
        await db.visual_bug_reports.update_one(
            {"report_id": report_id}, {"$set": {"status": "failed"}},
        )
        raise HTTPException(502, "Could not load the image to analyze")

    vision = await analyze_screenshot(
        image_bytes, content_type or doc.get("content_type", "image/png"), bug_description,
    )

    candidate_files: list[dict] = []
    if vision.get("ok") and project_id:
        candidate_files = await _correlate_codebase(
            user["user_id"], project_id,
            vision.get("extracted_strings") or [],
            vision.get("suggested_patterns") or [],
        )

    status = "analyzed" if vision.get("ok") else "failed"
    await db.visual_bug_reports.update_one(
        {"report_id": report_id},
        {"$set": {
            "status": status,
            "bug_description": bug_description,
            "project_id": project_id,
            "vision_analysis": vision,
            "candidate_files": candidate_files,
        }},
    )

    return {
        "ok": vision.get("ok", False),
        "id": report_id,
        "status": status,
        "error": vision.get("error"),
        "visual_defects": vision.get("visual_defects", []),
        "extracted_strings": vision.get("extracted_strings", []),
        "inferred_dom_elements": vision.get("inferred_dom_elements", []),
        "suggested_patterns": vision.get("suggested_patterns", []),
        "candidate_files": candidate_files,
    }


async def _correlate_codebase(user_id: str, project_id: str,
                               extracted_strings: list[str],
                               suggested_patterns: list[str]) -> list[dict]:
    """Deterministic Localization (founder's quality gate): combine
    literal-text grep (`search_repo`, exact OCR'd strings — highest
    confidence) with concept search (`semantic_search_repo`, GitHub
    Code Search + local TF-IDF fallback — lower confidence) instead of
    unstructured brute-force searching. Reuses the SAME tools the
    agent's own Loop Mode uses to read the connected repo — no new
    indexing system built for this."""
    from services.local_tools import search_repo, semantic_search_repo

    db = get_db()
    try:
        bin_ctx = await build_bin_context(user_id, project_id=project_id, db=db)
    except Exception as e:                                    # noqa: BLE001
        # build_bin_context raises HTTPException(400/403) for a
        # missing/foreign/unconnected project — codebase correlation
        # is a nice-to-have on top of the vision analysis, never a
        # reason to fail the whole /analyze call.
        logger.info("[visual_bugfix] codebase correlation skipped (no usable project): %r", e)
        return []
    ctx = {"user_id": user_id, "project_id": project_id, "bin_ctx": bin_ctx}

    scored: dict[str, dict] = {}

    def _bump(path: str, delta: float, source: str):
        row = scored.setdefault(path, {"path": path, "confidence": 0.0, "sources": []})
        row["confidence"] = min(1.0, row["confidence"] + delta)
        if source not in row["sources"]:
            row["sources"].append(source)

    # Highest confidence: the EXACT text visible in the screenshot
    # literally appears in a source file.
    for s in extracted_strings[:5]:
        if len(s) < 3:
            continue
        try:
            r = await search_repo(ctx, {"pattern": s, "max": 5})
        except Exception as e:                                # noqa: BLE001
            logger.debug("[visual_bugfix] search_repo failed for %r: %r", s, e)
            continue
        if r.get("ok"):
            for m in (r.get("matches") or [])[:5]:
                _bump(m.get("file"), 0.35, "extracted_text_match")

    # Lower confidence: conceptual/component-name search.
    for p in suggested_patterns[:5]:
        clean = p.strip("*").strip()
        if len(clean) < 3:
            continue
        try:
            r = await semantic_search_repo(ctx, {"query": clean, "max": 5})
        except Exception as e:                                # noqa: BLE001
            logger.debug("[visual_bugfix] semantic_search_repo failed for %r: %r", p, e)
            continue
        if r.get("ok"):
            for h in (r.get("results") or [])[:5]:
                _bump(h.get("path"), 0.15, "semantic_match")

    ranked = sorted(scored.values(), key=lambda x: x["confidence"], reverse=True)
    return ranked[:8]



# ── Phase 3: Patch Synthesizer & Isolated Preview ─────────────────────
# ── Phase 4: Before/After Action Bar (approve-pr / discard) ───────────
_MAX_REFINES = 3


async def _repo_creds_and_ctx(user_id: str, project_id: str) -> tuple[dict, str, str, str, str]:
    """Returns (ctx, owner, repo, branch, token) for a report's project.
    Lets build_bin_context's own HTTPException(400/403) propagate as-is
    for a missing/unconnected project — unlike Phase 2's optional
    correlation step, patch/preview/PR generation genuinely CANNOT
    proceed without a real repo connection, so this must hard-fail."""
    db = get_db()
    bin_ctx = await build_bin_context(user_id, project_id=project_id, db=db)
    if not (getattr(bin_ctx, "repo_owner", "") and getattr(bin_ctx, "repo_name", "")):
        raise HTTPException(400, "This project has no connected GitHub repo.")
    if not getattr(bin_ctx, "pat", ""):
        raise HTTPException(400, "GitHub App access is unavailable for this project — "
                                  "reconnect the repo via Projects → APP (AUREM GitHub App).")
    ctx = {"user_id": user_id, "project_id": project_id, "bin_ctx": bin_ctx}
    return ctx, bin_ctx.repo_owner, bin_ctx.repo_name, (bin_ctx.branch or "main"), bin_ctx.pat


def _unified_diff(path: str, old: str, new: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))


@router.post("/{report_id}/generate-patch")
async def generate_patch_endpoint(
    report_id: str,
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Phase 3 — one Gemini 3.1 Pro TEXT call (Cost Guard: on top of
    Phase 2's one vision call) that turns the vision analysis +
    ranked candidate files into a minimal, syntax-checked fix.
    "Refine Fix" (Phase 4) re-enters here with `refine_instruction`."""
    from services.visual_bugfix_patch import generate_patch

    user = await _resolve_user(authorization)
    doc = await _get_owned_report(report_id, user["user_id"])
    if doc.get("status") not in ("analyzed", "patch_generated", "preview_failed", "previewed"):
        raise HTTPException(400, f"Report is not ready for patch generation "
                                  f"(status={doc.get('status')}). Run /analyze first.")
    if not doc.get("candidate_files"):
        raise HTTPException(400, "No candidate source files found — connect a repo and re-run analysis.")

    refine_instruction = (body or {}).get("refine_instruction") or ""
    refine_count = int(doc.get("refine_count") or 0)
    if refine_instruction and refine_count >= _MAX_REFINES:
        raise HTTPException(429, f"Max {_MAX_REFINES} refinements reached for this report.")

    # Pre-Flight Quota Check — this is the point where the run's ONE
    # task gets counted (see services/usage.py's extended aggregation),
    # so re-check right before spending the patch-gen $ even though
    # /upload already checked once.
    await assert_has_task_budget(user["user_id"])

    project_id = doc.get("project_id")
    ctx, owner, repo, branch, token = await _repo_creds_and_ctx(user["user_id"], project_id)

    db = get_db()
    await db.visual_bug_reports.update_one(
        {"report_id": report_id}, {"$set": {"status": "patch_generating"}},
    )

    result = await generate_patch(
        candidate_files=doc.get("candidate_files") or [],
        vision_analysis=doc.get("vision_analysis") or {},
        bug_description=doc.get("bug_description") or "",
        refine_instruction=refine_instruction,
        ctx=ctx,
    )

    if not result.get("ok"):
        await db.visual_bug_reports.update_one(
            {"report_id": report_id}, {"$set": {"status": "analyzed"}},
        )
        return {"ok": False, "id": report_id, "error": result.get("error")}

    files_out = [{
        "path": f["path"],
        "diff": _unified_diff(f["path"], f["old_content"], f["new_content"]),
        "has_syntax_error": f["has_syntax_error"],
        "syntax_error": f["syntax_error"],
    } for f in result["files"]]
    has_syntax_errors = any(f["has_syntax_error"] for f in result["files"])

    update = {
        "status": "patch_generated",
        "patch_summary": result["summary"],
        "patch_commit_message": result["commit_message"],
        "patch_files": result["files"],   # full old/new content — needed by /preview and /approve-pr
        "patch_has_syntax_errors": has_syntax_errors,
        "owner": owner, "repo": repo, "branch": branch,
    }
    if refine_instruction:
        update["refine_count"] = refine_count + 1
    await db.visual_bug_reports.update_one({"report_id": report_id}, {"$set": update})

    # Accurate Ledger Tracking (observability only — the real quota
    # gate is the monthly task-count cap above, per usage.py's own
    # "headline pricing model is flat-fee" convention). Token counts
    # are a chars/4 ESTIMATE — emergentintegrations.llm.chat's LlmChat
    # does not expose real usage (see visual_bugfix_patch.py's
    # _estimate_tokens docstring for why).
    try:
        from services.ora_chat import cost_tracker
        await cost_tracker.log_call(
            user_id=user["user_id"], session_id=report_id, route="visual_bugfix.patch",
            model="gemini-3.1-pro-preview", temperature=0.0,
            input_tokens=result["estimated_tokens"], output_tokens=0,
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] token ledger log failed: %r", e)

    return {
        "ok": True, "id": report_id, "status": "patch_generated",
        "summary": result["summary"], "commit_message": result["commit_message"],
        "files": files_out, "has_syntax_errors": has_syntax_errors,
    }


@router.get("/{report_id}/status")
async def get_status(
    report_id: str,
    authorization: Optional[str] = Header(None),
):
    """Lightweight poll endpoint for the background /preview job — a
    real yarn/npm install can run past the ~60s external-proxy ceiling
    services/local_tools.py's own search_repo docstring already
    documents for this pod, so /preview returns immediately and the
    frontend polls here instead of holding one long request open."""
    user = await _resolve_user(authorization)
    doc = await _get_owned_report(report_id, user["user_id"])
    return {
        "ok": True,
        "id": report_id,
        "status": doc.get("status"),
        "preview_error": doc.get("preview_error"),
        "preview_stage": doc.get("preview_stage"),
        "cold_install": doc.get("preview_cold_install", False),
        "pr_url": doc.get("pr_url"),
    }


async def _run_preview_job(report_id: str, user_id: str, token: str) -> None:
    """Background task — see /preview's docstring for why this can't
    be a blocking request."""
    from services.visual_bugfix_preview import run_preview

    db = get_db()
    doc = await db.visual_bug_reports.find_one({"report_id": report_id})
    if not doc:
        return
    try:
        result = await run_preview(
            owner=doc["owner"], repo=doc["repo"], branch=doc["branch"], token=token,
            patch_files=doc.get("patch_files") or [],
            report_id=report_id, user_id=user_id,
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix] preview job crashed: %r", e)
        result = {"ok": False, "stage": "crashed", "preview_error": str(e), "cold_install": False}

    if result.get("ok"):
        after_path = f"visual-bugfix/{user_id}/{report_id}_after.png"
        try:
            await put_object(after_path, result["preview_image_bytes"], result["preview_image_content_type"])
        except Exception as e:                                # noqa: BLE001
            logger.warning("[visual_bugfix] after-image storage upload failed: %r", e)
            await db.visual_bug_reports.update_one(
                {"report_id": report_id},
                {"$set": {"status": "preview_failed",
                          "preview_error": "Could not store the preview image."}},
            )
            return
        await db.visual_bug_reports.update_one(
            {"report_id": report_id},
            {"$set": {
                "status": "previewed", "after_storage_path": after_path,
                "preview_cold_install": result.get("cold_install", False),
                "preview_error": None,
            }},
        )
    else:
        await db.visual_bug_reports.update_one(
            {"report_id": report_id},
            {"$set": {
                "status": "preview_failed",
                "preview_error": result.get("preview_error"),
                "preview_stage": result.get("stage"),
                "preview_cold_install": result.get("cold_install", False),
            }},
        )


@router.post("/{report_id}/preview")
async def preview_endpoint(
    report_id: str,
    authorization: Optional[str] = Header(None),
):
    """Phase 3 — kicks off the isolated headless preview build as a
    background task and returns immediately (see /status for polling).
    Fail-Safe Degradation: a failure here NEVER removes the
    already-computed code diff from /generate-patch — the report
    simply stays showable with status=preview_failed."""
    user = await _resolve_user(authorization)
    doc = await _get_owned_report(report_id, user["user_id"])
    if doc.get("status") not in ("patch_generated", "preview_failed", "previewed"):
        raise HTTPException(400, f"No patch to preview yet (status={doc.get('status')}). "
                                  "Run /generate-patch first.")
    if doc.get("patch_has_syntax_errors"):
        raise HTTPException(400, "The generated patch has a syntax error — fix via Refine before previewing.")

    _, _, _, _, token = await _repo_creds_and_ctx(user["user_id"], doc["project_id"])

    db = get_db()
    await db.visual_bug_reports.update_one(
        {"report_id": report_id}, {"$set": {"status": "preview_generating", "preview_error": None}},
    )
    asyncio.create_task(_run_preview_job(report_id, user["user_id"], token))
    return {"ok": True, "id": report_id, "status": "preview_generating"}


@router.post("/{report_id}/approve-pr")
async def approve_pr_endpoint(
    report_id: str,
    authorization: Optional[str] = Header(None),
):
    """Phase 4 — 'Approve & Create PR'. Opens a DRAFT PR (founder-
    approved default, 2026-09-12) on a fresh aurem/visual-bugfix-*
    branch, reusing the EXISTING Loop Mode branch/commit/PR primitives
    (services.loop_safety + services.github_api_writer) — no new git
    logic built. Never auto-merges — the user must click this
    explicitly, and the PR itself stays a draft until THEY mark it
    ready on GitHub."""
    from services.visual_bugfix_pr import approve_and_create_pr

    user = await _resolve_user(authorization)
    doc = await _get_owned_report(report_id, user["user_id"])
    if doc.get("status") not in ("patch_generated", "preview_failed", "previewed"):
        raise HTTPException(400, f"No patch ready to ship (status={doc.get('status')}).")
    if doc.get("patch_has_syntax_errors"):
        raise HTTPException(400, "The generated patch has a syntax error — fix via Refine before creating a PR.")

    _, _, _, _, token = await _repo_creds_and_ctx(user["user_id"], doc["project_id"])
    db = get_db()
    before_url = f"/api/aurem-dev/visual-bugfix/{report_id}/image?variant=before"
    after_url = (f"/api/aurem-dev/visual-bugfix/{report_id}/image?variant=after"
                 if doc.get("after_storage_path") else None)

    result = await approve_and_create_pr(
        owner=doc["owner"], repo=doc["repo"], base_branch=doc["branch"], token=token,
        patch_files=doc.get("patch_files") or [],
        commit_message=doc.get("patch_commit_message") or "",
        summary=doc.get("patch_summary") or "",
        bug_description=doc.get("bug_description") or "",
        report_id=report_id, before_image_url=before_url, after_image_url=after_url,
        db=db, user_id=user["user_id"],
    )
    if not result.get("ok"):
        return {"ok": False, "id": report_id, "error": result.get("error")}

    await db.visual_bug_reports.update_one(
        {"report_id": report_id},
        {"$set": {"status": "pr_created", "pr_url": result["pr_url"], "pr_branch": result["branch"]}},
    )
    return {"ok": True, "id": report_id, "status": "pr_created", "pr_url": result["pr_url"]}


@router.post("/{report_id}/discard")
async def discard_endpoint(
    report_id: str,
    authorization: Optional[str] = Header(None),
):
    """Phase 4 — 'Discard'. No GitHub side effects to undo (a branch
    is only ever created by /approve-pr, which the user must
    explicitly click) — this just closes the workbench server-side."""
    user = await _resolve_user(authorization)
    await _get_owned_report(report_id, user["user_id"])
    db = get_db()
    await db.visual_bug_reports.update_one(
        {"report_id": report_id}, {"$set": {"status": "discarded"}},
    )
    return {"ok": True, "id": report_id, "status": "discarded"}
