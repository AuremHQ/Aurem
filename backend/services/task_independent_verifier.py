"""
services/task_independent_verifier.py — Trust & Verification round,
Enhancement 1 (Independent Verification), extended to the single-edit
chat-task pipeline.

`services/loop_independent_verifier.py` already proved this pattern
for Loop Mode: a separate LLM call, in a fresh context, that never
sees the executor's own reasoning/plan, blunt pass/fail, fail-closed
on any parse/infra error. It is wired into `services/loop_engine.py`'s
ship gate and blocks a "no" verdict from ever committing.

That pattern did NOT previously cover the single-edit chat-task path
(routers/cto_projects/worker_api.py / worker_git.py) — the MOST common
real-world completion path — which only had
`services/vanguard_verify_agent.py` (a SECURITY-only second-agent
review). A patch could be security-clean and still not do what the
user asked, and nothing independent ever checked that. This module
closes that gap using the same properties, generalized:

  1. Separate LLM call, fresh context — sees only the task text and
     the final diff, never the executor's chain-of-thought.
  2. Fail-closed: any parse ambiguity or infra error -> "inconclusive"
     (or "fail" for a parseable-but-wrong verdict), NEVER "pass".
  3. `verified_by` (this verifier's own model identity) is asserted to
     differ from `executed_by` (the executor's identity) at the
     shared ledger's write choke point (services/verification.py) —
     an executor cannot grade its own work even by accident.

Returns exactly one of `pass | fail | inconclusive` (Enhancement 1's
vocabulary) and ALWAYS appends one row to the shared
`task_verifications` ledger (services/verification.py), regardless of
outcome — even "inconclusive" is durably recorded, never silently
dropped.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

from services.ora_chat.providers import one_shot as _one_shot
from services import verification as _v
from services.task_diff import build_unified_diff_hunks as _build_hunks

logger = logging.getLogger(__name__)

try:
    from services.llm.openrouter_providers import _CLAUDE_MODEL as _SSOT_CLAUDE
except Exception as _e:                                       # noqa: BLE001
    logger.warning("task_independent_verifier: SSOT model import failed (%r)", _e)
    _SSOT_CLAUDE = "anthropic/claude-sonnet-4.5"

_DEFAULT_MODEL = os.getenv("VERIFIER_MODEL", _SSOT_CLAUDE)
_MAX_DIFF_CHARS = 20_000
_MAX_FILES_SHOWN = 12

_SYSTEM = (
    "You are an independent code reviewer. You are shown the user's "
    "ORIGINAL request and a real unified diff (old vs new, with "
    "context lines around each hunk; brand-new files show their full "
    "content) that a coding agent is about to ship. You do NOT see "
    "the agent's reasoning, plan, or self-assessment. Your only job: "
    "does this diff actually do what the user asked?\n\n"
    "Rules:\n"
    "- If the diff clearly implements the request, say PASS.\n"
    "- If the diff does something else, is incomplete, or is empty/"
    "unrelated to the request, say FAIL.\n"
    "- Be blunt. Do not defend the agent's work.\n"
    "- Some hunks or files may be marked '(omitted for size)' below. "
    "If the part of the diff that would show the requested change is "
    "missing because of that, you cannot know it is wrong — say "
    'INCONCLUSIVE, never FAIL, for that specific situation.\n\n'
    'Reply in strict JSON on one line: '
    '{"verdict":"pass"|"fail"|"inconclusive","reason":"one line, max 200 chars"}\n'
    "No prose outside the JSON."
)


def _format_diff(before: dict, after: dict) -> tuple:
    """Real unified diff: old vs new content, with context lines
    around each changed hunk (via services.task_diff's shared
    difflib-based hunk builder — same engine the live task UI already
    uses to show diffs to the user). Brand-new files (no entry in
    `before`) get their full content, line-prefixed with '+'.

    The total-size budget is enforced, but a cut can only land on a
    whole-hunk boundary (or a whole-line boundary for a new file) —
    never mid-line, so a line is either fully shown or fully omitted.
    Returns (diff_text, any_omitted) — `any_omitted` is True if ANY
    hunk, new-file tail, or whole file had to be left out, so the
    caller can refuse to accept a "fail" verdict built on a partial
    view.
    """
    if not after:
        return "(no files in diff)", False

    before = before or {}
    items = list(after.items())
    parts: list = []
    used = 0
    any_omitted = False
    files_shown = 0

    for path, new_content in items:
        if files_shown >= _MAX_FILES_SHOWN:
            any_omitted = True
            break

        header = f"--- {path} (before)\n+++ {path} (after)\n"
        old_content = before.get(path)

        if old_content is None:
            # Brand-new file → full content, cut only at line boundaries.
            lines = (new_content or "").splitlines()
            kept: list = []
            for ln in lines:
                candidate_len = used + len(header) + len("\n".join(kept + [ln])) + 1
                if candidate_len > _MAX_DIFF_CHARS:
                    break
                kept.append(ln)
            omitted_lines = len(lines) - len(kept)
            body = "\n".join(f"+{ln}" for ln in kept)
            block = header + body
            if omitted_lines > 0:
                block += f"\n… ({omitted_lines} more line(s) of this new file omitted for size)"
        else:
            hunks = _build_hunks(old_content, new_content, context=3)
            hunk_texts: list = []
            omitted_hunks = 0
            for h in hunks:
                hb = [f"@@ -{h['old_start']} +{h['new_start']} @@"]
                for ln in h["lines"]:
                    hb.append(f"{ln['tag']}{ln['text']}")
                hunk_text = "\n".join(hb)
                projected = used + len(header) + sum(len(x) + 1 for x in hunk_texts) + len(hunk_text)
                if projected > _MAX_DIFF_CHARS:
                    omitted_hunks += 1
                    continue
                hunk_texts.append(hunk_text)
            if not hunk_texts and not omitted_hunks:
                hunk_texts = ["(no textual difference)"]
            block = header + "\n".join(hunk_texts)
            if omitted_hunks > 0:
                block += f"\n… ({omitted_hunks} more hunk(s) of this file omitted for size)"

        if used + len(block) > _MAX_DIFF_CHARS:
            # Doesn't fit at all even on its own — omit the whole file,
            # never emit a mid-line/mid-hunk fragment of it.
            any_omitted = True
            parts.append(f"--- {path} --- (omitted entirely: too large for the remaining size budget)")
            files_shown += 1
            continue

        if "omitted" in block:
            any_omitted = True
        parts.append(block)
        used += len(block)
        files_shown += 1

    if len(items) > files_shown:
        any_omitted = True
        parts.append(f"…(+{len(items) - files_shown} more file(s) not shown)")

    return "\n\n".join(parts), any_omitted


def _parse(raw: str) -> tuple:
    """Fail-closed: any parse ambiguity -> ("fail", "verifier_parse_error")."""
    s = (raw or "").strip()
    s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        j = json.loads(s)
    except ValueError:
        return "fail", "verifier_parse_error"
    v = str(j.get("verdict") or "").strip().lower()
    r = str(j.get("reason") or "").strip()[:200]
    if v not in ("pass", "fail", "inconclusive"):
        return "fail", "verifier_parse_error"
    return v, r or "(no reason)"


async def verify_task_completion(
    db, *, task_id: str, project_id: Optional[str], user_id: Optional[str],
    task_text: str, files: dict, executed_by: str,
    before_files: Optional[dict] = None,
    verifier_model: Optional[str] = None, origin: str = "chat_task",
) -> dict:
    """Non-raising. ALWAYS returns the recorded ledger row (plus
    `latency_s`) and ALWAYS appends exactly one row to
    `task_verifications` via services.verification.record_verification
    — a fail-closed audit trail even on infra error.

    `before_files` is the pre-edit content of every touched file
    (same shape as `files`, the post-edit content) — pass the
    `contents` dict the caller already has. Without it, every file
    reads as brand-new, which is still correct (just less precise:
    full content instead of a diff) but should only happen for a
    caller that genuinely has no pre-edit content available.
    """
    started = time.time()
    model = verifier_model or _DEFAULT_MODEL
    verified_by = f"independent_verifier:{model}"
    revision_id = _v.compute_task_revision_id(task_text, files)

    if not (task_text or "").strip() or not files:
        row = await _v.record_verification(
            db, task_id=task_id, project_id=project_id, user_id=user_id,
            task_revision_id=revision_id, verified_by=verified_by,
            executed_by=executed_by, decision=_v.DECISION_INCONCLUSIVE,
            reason="no_task_text_or_no_files", origin=origin,
        )
        return {**row, "latency_s": round(time.time() - started, 2)}

    diff_text, any_omitted = _format_diff(before_files, files)
    omit_note = (
        "\n\nNOTE: part of this diff was omitted above because of size "
        "limits — see the '(omitted for size)' markers. If the change "
        "the user asked for would only be visible in an omitted part, "
        "say INCONCLUSIVE, not FAIL."
        if any_omitted else ""
    )
    user_prompt = (
        "USER'S ORIGINAL REQUEST:\n" + (task_text or "") + "\n\n"
        "UNIFIED DIFF ABOUT TO SHIP (old vs new, with context lines; "
        "brand-new files show full content):\n"
        + diff_text + omit_note
        + "\n\nAnswer strictly in JSON as instructed."
    )
    try:
        text, usage, err = await _one_shot(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0, top_p=1.0, presence_penalty=0.0, max_tokens=200,
        )
    except Exception as e:                                   # noqa: BLE001
        row = await _v.record_verification(
            db, task_id=task_id, project_id=project_id, user_id=user_id,
            task_revision_id=revision_id, verified_by=verified_by,
            executed_by=executed_by, decision=_v.DECISION_INCONCLUSIVE,
            reason=f"verifier_exception:{type(e).__name__}", origin=origin,
        )
        return {**row, "latency_s": round(time.time() - started, 2)}

    if err or not (text or "").strip():
        row = await _v.record_verification(
            db, task_id=task_id, project_id=project_id, user_id=user_id,
            task_revision_id=revision_id, verified_by=verified_by,
            executed_by=executed_by, decision=_v.DECISION_INCONCLUSIVE,
            reason=err or "empty_verifier_response", origin=origin,
        )
        return {**row, "latency_s": round(time.time() - started, 2)}

    verdict, reason = _parse(text)
    if verdict == "pass":
        decision = _v.DECISION_PASS
    elif verdict == "inconclusive":
        decision = _v.DECISION_INCONCLUSIVE
    else:
        decision = _v.DECISION_FAIL
    # Rule: a verifier that could not see (part of) the change must
    # never reject it outright — enforced here in code, not left to
    # the model to remember. Any omission + a "fail" verdict becomes
    # "inconclusive" instead, regardless of what the model said.
    if any_omitted and decision == _v.DECISION_FAIL:
        decision = _v.DECISION_INCONCLUSIVE
        reason = f"fail_overridden_incomplete_view: {reason}"[:200]
    # Level answers "what was checked", independent of the verdict —
    # a real sandbox check ran either way. Only "inconclusive" (no
    # check actually completed) gets no level at all.
    level = _v.LEVEL_SANDBOX_REPRODUCED
    row = await _v.record_verification(
        db, task_id=task_id, project_id=project_id, user_id=user_id,
        task_revision_id=revision_id, verified_by=verified_by,
        executed_by=executed_by, decision=decision, reason=reason,
        verification_level=level, origin=origin,
    )
    return {**row, "latency_s": round(time.time() - started, 2)}
