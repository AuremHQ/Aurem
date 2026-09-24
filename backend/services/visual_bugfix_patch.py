"""
services/visual_bugfix_patch.py — Phase 3 Patch Synthesizer for the
Multimodal Visual Bug-Fix pipeline. ONE Gemini 3.1 Pro text-generation
call per run (Cost Guard, on top of Phase 2's one vision call).

Founder-approved (2026-09-12): same provider/key as the Phase 2 vision
step (Gemini 3.1 Pro via the Emergent Universal Key) — one integration,
one error-handling layer, no extra setup.

Design choice: the model returns FULL new file bodies (not a raw
unified-diff text blob) — same convention `write_repo_file` already
uses elsewhere in this codebase ("content = FULL new file body, we do
not patch in-place"). This sidesteps the well-known unreliability of
LLM-generated unified diffs failing to apply cleanly. The actual
unified diff shown to the user (Phase 4 UI) is computed locally via
`difflib`, not asked of the model.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid

logger = logging.getLogger(__name__)

_PATCH_MODEL = "gemini-3.1-pro-preview"
_MAX_FILES_IN_PROMPT = 3
_MAX_FILE_CHARS = 8000

_SYSTEM_PROMPT = (
    "You are a precise, minimal-diff UI bug-fix engine. You are given a "
    "user's bug description, a vision model's extraction of what's "
    "visibly wrong in a screenshot, and the CURRENT full content of the "
    "most likely source file(s). Your job: fix ONLY the described visual "
    "defect, changing as little else as possible. Do not refactor, "
    "reformat, or rename anything you weren't asked to touch.\n\n"
    "Respond with a SINGLE JSON object, no prose before or after, no "
    "markdown code fences, matching EXACTLY this shape:\n"
    "{\n"
    '  "summary": "<one sentence, what you changed and why>",\n'
    '  "commit_message": "<short conventional-commit style message, e.g. \\"fix: correct button alignment in Checkout\\">",\n'
    '  "files": [\n'
    '    {"path": "<exact path as given>", "new_content": "<COMPLETE new file body, not a diff>"}\n'
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "  - Only include a file in `files` if you actually changed it.\n"
    "  - `new_content` must be the ENTIRE file from the first line to the "
    "last — never a snippet, never \"...rest unchanged...\".\n"
    "  - If none of the given files actually contain the bug, return an "
    "empty `files` array and explain why in `summary`.\n"
)


def _extract_json_object(raw: str) -> dict | None:
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


def _estimate_tokens(*texts: str) -> int:
    """Honest disclosed limitation: emergentintegrations.llm.chat's
    LlmChat does not expose real prompt/completion token counts through
    any public (or private-but-stable) surface — it wraps litellm
    internally but only returns the response text. This is a coarse
    chars/4 heuristic for cost-observability logging, NOT used for any
    hard billing decision (the actual quota gate is the monthly
    task-count cap via services.usage.assert_has_task_budget)."""
    return sum(max(1, len(t or "")) for t in texts) // 4


async def generate_patch(*, candidate_files: list[dict], vision_analysis: dict,
                          bug_description: str, refine_instruction: str,
                          ctx: dict) -> dict:
    """Fetch the top candidate files' current content, ask Gemini 3.1
    Pro for a minimal fix, syntax-check every returned file.

    Returns:
      {ok, summary, commit_message, files: [{path, old_content,
       new_content, has_syntax_error, syntax_error}], estimated_tokens,
       error?}
    Never raises.
    """
    from services.local_tools import read_repo_files, _run_syntax_check

    top_paths = [c["path"] for c in (candidate_files or [])[:_MAX_FILES_IN_PROMPT]]
    if not top_paths:
        return {"ok": False, "error": "No candidate files to patch — "
                "connect a repo and re-run analysis first.",
                "summary": "", "commit_message": "", "files": [],
                "estimated_tokens": 0}

    read_result = await read_repo_files(ctx, {"paths": top_paths})
    ok_files = {f["path"]: f["content"] for f in (read_result.get("files") or [])
                if f.get("ok")}
    if not ok_files:
        return {"ok": False, "error": "Could not read any candidate file "
                f"from the connected repo ({read_result.get('errors')}).",
                "summary": "", "commit_message": "", "files": [],
                "estimated_tokens": 0}

    key = (os.environ.get("EMERGENT_LLM_KEY") or "").strip()
    if not key:
        return {"ok": False, "error": "EMERGENT_LLM_KEY not set",
                "summary": "", "commit_message": "", "files": [],
                "estimated_tokens": 0}

    from emergentintegrations.llm.chat import LlmChat, UserMessage

    file_blocks = "\n\n".join(
        f"--- FILE: {p} ---\n{content[:_MAX_FILE_CHARS]}"
        for p, content in ok_files.items()
    )
    defects = ", ".join(vision_analysis.get("visual_defects") or []) or "(none listed)"
    strings = ", ".join(vision_analysis.get("extracted_strings") or []) or "(none)"
    dom_hints = ", ".join(vision_analysis.get("inferred_dom_elements") or []) or "(none)"

    prompt = (
        f"User's bug description: {bug_description or '(none given)'}\n"
        f"Vision-detected defects: {defects}\n"
        f"Text visible in the screenshot: {strings}\n"
        f"Inferred DOM/element hints: {dom_hints}\n"
        + (f"User's follow-up refine instruction: {refine_instruction}\n" if refine_instruction else "")
        + f"\nCandidate source files (current content):\n{file_blocks}\n\n"
          "Return the fix JSON now."
    )

    chat = (
        LlmChat(
            api_key=key,
            session_id=f"visual-bugfix-patch-{uuid.uuid4().hex[:8]}",
            system_message=_SYSTEM_PROMPT,
        )
        .with_model("gemini", _PATCH_MODEL)
    )
    try:
        raw = await chat.send_message(UserMessage(text=prompt))
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix_patch] LLM call failed: %r", e)
        return {"ok": False, "error": f"patch generation failed: {type(e).__name__}: {e}",
                "summary": "", "commit_message": "", "files": [],
                "estimated_tokens": _estimate_tokens(prompt)}

    parsed = _extract_json_object(raw)
    estimated_tokens = _estimate_tokens(prompt, raw)
    if parsed is None:
        return {"ok": False, "error": "model returned no parseable JSON",
                "summary": "", "commit_message": "", "files": [],
                "estimated_tokens": estimated_tokens}

    out_files = []
    for f in (parsed.get("files") or [])[:_MAX_FILES_IN_PROMPT]:
        path = str(f.get("path") or "").strip()
        new_content = f.get("new_content")
        if not path or path not in ok_files or not isinstance(new_content, str):
            continue
        ext = os.path.splitext(path)[1]
        syn = _run_syntax_check(content=new_content, file_path=path, ext=ext)
        out_files.append({
            "path": path,
            "old_content": ok_files[path],
            "new_content": new_content,
            "has_syntax_error": bool(syn.get("has_errors")),
            "syntax_error": syn.get("errors") or None,
        })

    if not out_files:
        return {"ok": False,
                "error": str(parsed.get("summary") or "Model made no changes to any candidate file."),
                "summary": str(parsed.get("summary") or ""),
                "commit_message": "", "files": [],
                "estimated_tokens": estimated_tokens}

    return {
        "ok": True,
        "summary": str(parsed.get("summary") or ""),
        "commit_message": str(parsed.get("commit_message") or "fix: visual bug fix via AUREM")[:200],
        "files": out_files,
        "estimated_tokens": estimated_tokens,
    }
