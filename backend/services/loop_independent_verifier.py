"""
services/loop_independent_verifier.py — Iter 272 Feature 1.3

Runs AFTER Vanguard has already passed. Its job is narrow: given the
frozen task spec (loop_task_specs) and the actual diff about to ship,
decide whether the diff satisfies the ORIGINAL acceptance criteria.
Yes / no + one-line reason.

Critical properties:
  1. It's a SEPARATE LLM call — a fresh context. It never sees the
     fixing agent's chain-of-thought, plan revisions, or Vanguard's
     verdict. The only inputs are the frozen spec and the raw diff.
  2. It uses a DIFFERENT model family from the drafter/executor when
     possible (`VERIFIER_MODEL` env override, else defaults to
     `anthropic/claude-sonnet-4.5` via OpenRouter — reused, cheap).
  3. It NEVER auto-passes on parser errors. If we can't parse the
     verifier's output → verdict is treated as "no" (fail-closed).
  4. If the OpenRouter key is missing, verdict is treated as
     `skipped_no_llm` — NOT "yes". The caller must decide whether
     to block or allow-with-warning.

Public surface:
    async verify(db, *, loop_id, files) → dict
      { verdict: "yes"|"no"|"skipped_no_llm"|"skipped_no_spec",
        reason:  str,
        verifier_model: str,
        latency_s: float,
        raw: str,   # verifier's full response for audit
      }
Also writes one row to `loop_verification_log` on every call
(success OR skip). Never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

from services.ora_chat.providers import one_shot as _one_shot
from services.loop_task_specs import get as _get_spec

logger = logging.getLogger(__name__)

_COLL = "loop_verification_log"

# SSOT-refactor (Feb 2026) — pull Claude model slug from services.llm
# so this verifier cannot drift from Parliament V2's canonical ID.
# Env override still honoured for per-deploy pinning without redeploy.
try:
    from services.llm.openrouter_providers import _CLAUDE_MODEL as _SSOT_CLAUDE
except Exception as _e:  # pragma: no cover — defensive on import cycles
    logger.warning("loop_independent_verifier: SSOT model import failed (%r)", _e)
    _SSOT_CLAUDE = "anthropic/claude-sonnet-4.5"

_DEFAULT_MODEL = os.getenv("VERIFIER_MODEL", _SSOT_CLAUDE)
_MAX_DIFF_CHARS = 20_000        # keep the verifier prompt bounded
_MAX_FILES_SHOWN = 12

_SYSTEM = (
    "You are an independent code reviewer. You are shown the original "
    "user task (with acceptance criteria) and the diff about to ship. "
    "You do NOT see the coding agent's reasoning, plan, or the prior "
    "verifier's output. Your only job is to answer one question:\n\n"
    "  Does this diff satisfy the ORIGINAL acceptance criteria?\n\n"
    "Rules:\n"
    "- If the diff clearly implements what the task asked for, say YES.\n"
    "- If the diff does something else, is incomplete, or only modifies "
    "TEST files without touching the production code that would satisfy "
    "the task, say NO.\n"
    "- If the diff is empty or unrelated to the task, say NO.\n"
    "- Be blunt. Do not defend either side.\n\n"
    "Reply in strict JSON on a single line:\n"
    '  {"verdict": "yes" | "no", "reason": "one line, max 200 chars"}\n'
    "No prose outside the JSON."
)


def _format_diff(files: list[dict]) -> str:
    if not files:
        return "(no files in diff)"
    parts: list[str] = []
    used = 0
    for i, f in enumerate(files[:_MAX_FILES_SHOWN]):
        path = (f or {}).get("path") or "?"
        content = ((f or {}).get("content") or "")
        # Trim per-file so no single monster file eats the whole prompt.
        head = content[:2500]
        block = f"── {path} ──\n{head}"
        if used + len(block) > _MAX_DIFF_CHARS:
            block = block[: max(_MAX_DIFF_CHARS - used, 0)]
        parts.append(block)
        used += len(block)
        if used >= _MAX_DIFF_CHARS:
            parts.append(f"…(truncated, {len(files) - i - 1} more files)")
            break
    if len(files) > _MAX_FILES_SHOWN:
        parts.append(f"…(+{len(files) - _MAX_FILES_SHOWN} more file(s) not shown)")
    return "\n\n".join(parts)


def _parse_verdict(raw: str) -> tuple[str, str]:
    """Fail-closed. Any parse issue → ("no", "verifier_parse_error")."""
    s = (raw or "").strip()
    s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # Grab the first JSON object we can find (models occasionally
    # wrap in prose despite the instruction).
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        j = json.loads(s)
    except ValueError:
        return "no", "verifier_parse_error"
    v = str(j.get("verdict") or "").strip().lower()
    r = str(j.get("reason") or "").strip()[:200]
    if v not in ("yes", "no"):
        return "no", "verifier_parse_error"
    return v, r or "(no reason)"


async def _log_row(db, row: dict) -> None:
    try:
        await db[_COLL].insert_one(dict(row))
    except Exception as e:                                    # noqa: BLE001
        logger.warning("verification log insert failed: %r", e)


_VERDICT_TO_DECISION = {
    "yes": "pass", "no": "fail",
    "skipped_no_llm": "inconclusive", "skipped_no_spec": "inconclusive",
}


async def _mirror_to_shared_ledger(
    db, *, loop_id: str, model: str, verdict: str, reason: str,
    user_id, project_id, original: str, criteria: list, origin: str,
) -> None:
    """Trust & Verification round — additive mirror into the SAME
    shared `task_verifications` ledger (services/verification.py) the
    single-edit chat-task pipeline writes to
    (services/task_independent_verifier.py), so Enhancement 4's
    trust-calibration metric and Enhancement 3's retroactive-downgrade
    history see ONE unified decision trail regardless of which
    pipeline produced a given task's completion — without changing
    this module's own `loop_verification_log` row shape or callers
    (services/loop_engine.py's ship gate keeps reading THIS function's
    original return value exactly as before)."""
    try:
        from services import verification as _v
        task_text = original or ""
        if criteria:
            task_text += "\n" + "\n".join(f"- {c}" for c in criteria)
        revision_id = _v.compute_task_revision_id(task_text, {})
        decision = _VERDICT_TO_DECISION.get(verdict, "inconclusive")
        level = _v.LEVEL_SANDBOX_REPRODUCED if decision in ("pass", "fail") else None
        await _v.record_verification(
            db, task_id=loop_id, project_id=project_id, user_id=user_id,
            task_revision_id=revision_id,
            verified_by=f"independent_verifier:{model}",
            executed_by="loop_engine:executor", decision=decision,
            reason=reason, verification_level=level, origin=f"loop_mode:{origin}",
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("loop_independent_verifier: shared-ledger mirror failed: %r", e)


async def verify(db, *, loop_id: str,
                 files: list[dict],
                 verifier_model: Optional[str] = None,
                 origin: str = "loop_mode") -> dict:
    """Non-raising. Always returns a dict; always writes one row to
    `loop_verification_log`.

    `origin` is stamped onto every audit row so the same collection
    can host multiple review modes (loop-mode diff review vs Personal
    Track scaffold design review) without cross-mode pollution when
    querying counts/rates. Any consumer of `loop_verification_log`
    MUST filter by `origin`.
    """
    started = time.time()
    model = verifier_model or _DEFAULT_MODEL

    spec = await _get_spec(db, loop_id)
    if not spec:
        row = {
            "loop_id":        loop_id,
            "verifier_model": model,
            "verdict":        "skipped_no_spec",
            "reason":         "loop_task_specs missing — cannot verify",
            "latency_s":      round(time.time() - started, 2),
            # 2026-08-27 — TTL fix: real BSON Date (was `.isoformat()`
            # string — the `loop_verification_log.created_at` TTL
            # index never expired those rows).
            "created_at":     datetime.now(timezone.utc),
            "raw":            "",
            "origin":         origin,
        }
        await _log_row(db, row)
        await _mirror_to_shared_ledger(
            db, loop_id=loop_id, model=model, verdict="skipped_no_spec",
            reason=row["reason"], user_id=None, project_id=None,
            original="", criteria=[], origin=origin,
        )
        return row

    criteria = spec.get("acceptance_criteria") or []
    original = spec.get("original_task") or ""
    user_prompt = (
        "ORIGINAL TASK:\n"
        f"{original}\n\n"
        "ACCEPTANCE CRITERIA:\n"
        + "\n".join(f"- {c}" for c in criteria)
        + "\n\nDIFF TO SHIP (files with new content):\n"
        + _format_diff(files)
        + "\n\nAnswer strictly in JSON as instructed."
    )
    try:
        text, usage, err = await _one_shot(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.0,
            top_p=1.0,
            presence_penalty=0.0,
            max_tokens=200,
        )
    except Exception as e:                                    # noqa: BLE001
        row = {
            "loop_id":        loop_id,
            "verifier_model": model,
            "verdict":        "skipped_no_llm",
            "reason":         f"verifier_exception:{type(e).__name__}",
            "latency_s":      round(time.time() - started, 2),
            "created_at":     datetime.now(timezone.utc),
            "raw":            str(e)[:400],
            "origin":         origin,
        }
        await _log_row(db, row)
        await _mirror_to_shared_ledger(
            db, loop_id=loop_id, model=model, verdict="skipped_no_llm",
            reason=row["reason"], user_id=spec.get("user_id"),
            project_id=spec.get("project_id"), original=original,
            criteria=criteria, origin=origin,
        )
        return row

    if err or not (text or "").strip():
        row = {
            "loop_id":        loop_id,
            "verifier_model": model,
            "verdict":        "skipped_no_llm",
            "reason":         err or "empty_verifier_response",
            "latency_s":      round(time.time() - started, 2),
            "usage":          usage or {},
            "created_at":     datetime.now(timezone.utc),
            "raw":            (text or "")[:400],
            "origin":         origin,
        }
        await _log_row(db, row)
        await _mirror_to_shared_ledger(
            db, loop_id=loop_id, model=model, verdict="skipped_no_llm",
            reason=row["reason"], user_id=spec.get("user_id"),
            project_id=spec.get("project_id"), original=original,
            criteria=criteria, origin=origin,
        )
        return row

    verdict, reason = _parse_verdict(text)
    row = {
        "loop_id":        loop_id,
        "verifier_model": model,
        "verdict":        verdict,
        "reason":         reason,
        "latency_s":      round(time.time() - started, 2),
        "usage":          usage or {},
        "created_at":     datetime.now(timezone.utc),
        "raw":            (text or "")[:2000],
        "origin":         origin,
    }
    await _log_row(db, row)
    await _mirror_to_shared_ledger(
        db, loop_id=loop_id, model=model, verdict=verdict, reason=reason,
        user_id=spec.get("user_id"), project_id=spec.get("project_id"),
        original=original, criteria=criteria, origin=origin,
    )
    return row


async def ensure_indexes(db) -> None:
    await db[_COLL].create_index("loop_id")
    await db[_COLL].create_index("created_at")
    await db[_COLL].create_index("verdict")
    await db[_COLL].create_index("origin")


async def backfill_origin(db) -> dict:
    """One-shot: any legacy row missing `origin` is stamped
    `loop_mode` (the only mode that existed before Iter 274).
    Idempotent — reruns are cheap no-ops. Returns before/after counts."""
    before = await db[_COLL].count_documents({"origin": {"$exists": False}})
    if before == 0:
        return {"before": 0, "updated": 0, "after": 0}
    res = await db[_COLL].update_many(
        {"origin": {"$exists": False}},
        {"$set": {"origin": "loop_mode"}},
    )
    after = await db[_COLL].count_documents({"origin": {"$exists": False}})
    return {"before": before, "updated": res.modified_count, "after": after}
