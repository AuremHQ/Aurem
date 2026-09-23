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
    "ORIGINAL request and the diff a coding agent is about to ship. "
    "You do NOT see the agent's reasoning, plan, or self-assessment. "
    "Your only job: does this diff actually do what the user asked?\n\n"
    "Rules:\n"
    "- If the diff clearly implements the request, say PASS.\n"
    "- If the diff does something else, is incomplete, or is empty/"
    "unrelated to the request, say FAIL.\n"
    "- Be blunt. Do not defend the agent's work.\n\n"
    'Reply in strict JSON on one line: {"verdict":"pass"|"fail","reason":"one line, max 200 chars"}\n'
    "No prose outside the JSON."
)


def _format_diff(files: dict) -> str:
    if not files:
        return "(no files in diff)"
    parts: list = []
    used = 0
    items = list(files.items())
    for i, (path, content) in enumerate(items[:_MAX_FILES_SHOWN]):
        head = (content or "")[:2500]
        block = f"── {path} ──\n{head}"
        if used + len(block) > _MAX_DIFF_CHARS:
            block = block[: max(_MAX_DIFF_CHARS - used, 0)]
        parts.append(block)
        used += len(block)
        if used >= _MAX_DIFF_CHARS:
            parts.append(f"…(truncated, {len(items) - i - 1} more files)")
            break
    if len(items) > _MAX_FILES_SHOWN:
        parts.append(f"…(+{len(items) - _MAX_FILES_SHOWN} more file(s) not shown)")
    return "\n\n".join(parts)


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
    if v not in ("pass", "fail"):
        return "fail", "verifier_parse_error"
    return v, r or "(no reason)"


async def verify_task_completion(
    db, *, task_id: str, project_id: Optional[str], user_id: Optional[str],
    task_text: str, files: dict, executed_by: str,
    verifier_model: Optional[str] = None, origin: str = "chat_task",
) -> dict:
    """Non-raising. ALWAYS returns the recorded ledger row (plus
    `latency_s`) and ALWAYS appends exactly one row to
    `task_verifications` via services.verification.record_verification
    — a fail-closed audit trail even on infra error."""
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

    user_prompt = (
        "USER'S ORIGINAL REQUEST:\n" + (task_text or "") + "\n\n"
        "DIFF ABOUT TO SHIP (files with new content):\n"
        + _format_diff(files)
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
    decision = _v.DECISION_PASS if verdict == "pass" else _v.DECISION_FAIL
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
