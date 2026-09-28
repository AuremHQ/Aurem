"""
services/cto_projects_helpers.py — safe mechanical extraction from
routers/cto_projects.py (2026-08-26 coverage-floor extraction batch).

Pure/standalone helpers moved out verbatim (zero logic changes) to
shrink routers/cto_projects.py. Re-exported at the top of
`routers/cto_projects.py` so every existing bare-name call site inside
its endpoints/workers, every `from routers.cto_projects import X`, and
every `patch("routers.cto_projects.X", ...)` in the pre-existing test
suite keep working unchanged (Python re-export semantics).

NOT moved (deliberately, same posture as chat.py's `chat_stream` /
loop_engine.py's `LoopEngine`): `_run_task_via_api`, `_run_task_with_git`,
`_run_task`, `_run_rollback`, `_run_rollback_via_api`,
`_run_rollback_with_git`, `_rollback_log`, `_enqueue_cto_task` — the
actual git/API worker execution + rollback pipelines. Several of these
read the module-level `_GIT_AVAILABLE` flag and are patched directly by
existing tests at `routers.cto_projects.*`; moving them would require a
shared-mutable-flag or lazy-import workaround for no real maintainability
gain, so they stay put. `_frontend_subset`, `get_repo_token`, and
`_run_warm_agents` also stay — 3 existing tests do a literal
`open(cto_projects.py).read()` source-text check for their exact
definitions.
"""
from __future__ import annotations
import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Live progress streams (Iter 73) ──────────────────────────────────────
# In-memory per-task asyncio.Queue used to fan out worker steps to the
# /cto/tasks/{id}/stream SSE endpoint so chat bubbles render a live
# "worker tape" (reading files… thinking… committing…) instead of a
# silent spinner.  Queues are popped once the task emits a done/fail
# terminal frame, or when the stream times out (5 min wall-clock).
_task_queues: dict[str, asyncio.Queue] = {}


async def _emit(task_id: str, step: str,
                kind: str = "step", pct: Optional[int] = None,
                **extra) -> None:
    """Push one progress frame onto the task's live SSE queue.

    Non-blocking; safe to call even if no consumer is listening (the queue
    just buffers up to 256 frames then drops oldest).

    Any **extra keyword args are merged into the frame so callers can ship
    structured payloads (e.g. `agents=["backend","frontend"]` for the
    parallel-mode worker tape)."""
    if not task_id:
        return
    q = _task_queues.get(task_id)
    if q is None:
        q = asyncio.Queue(maxsize=256)
        _task_queues[task_id] = q
    frame = {"type": kind, "step": step, "pct": pct, "ts": time.time()}
    if extra:
        # Don't let callers overwrite the canonical fields.
        for k, v in extra.items():
            if k not in frame:
                frame[k] = v
    try:
        q.put_nowait(frame)
    except asyncio.QueueFull:
        # Drop the oldest frame to make room for the new one rather than
        # blocking the worker — the SSE client will see a small gap.
        try:
            q.get_nowait()
            q.put_nowait(frame)
        except Exception:
            pass


def _parse_repo(url: str) -> tuple[str, str]:
    from fastapi import HTTPException
    p = url.rstrip("/").replace(".git", "").replace("https://github.com/", "").split("/")
    if len(p) < 2:
        raise HTTPException(400, "Bad GitHub URL — expected https://github.com/owner/repo")
    return p[0], p[1]


async def _run_project_indexing(
    *, db, project_id: str, user_id: str,
    github_token: str, github_owner: str, github_repo: str, branch: str,
) -> None:
    """Background indexing wrapper for Iter 212m-75.

    Runs build_brain_v2 and writes the result to cto_projects so the
    FE polling endpoint /indexing-status can report progress.
    Errors are swallowed and persisted as `indexing_error`.
    """
    try:
        from services.project_brain import build_brain_v2
        await build_brain_v2(
            db=db, project_id=project_id, user_id=user_id,
            github_token=github_token, github_owner=github_owner,
            github_repo=github_repo, branch=branch,
        )
        await db.cto_projects.update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": {
                "indexing_status": "ready",
                "indexed_at":      time.time(),
                "indexing_error":  None,
            }},
        )
        logger.info("project indexing complete: %s", project_id)
    except Exception as e:
        logger.warning("project indexing failed for %s: %r", project_id, e)
        await db.cto_projects.update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": {
                "indexing_status": "error",
                "indexing_error":  str(e)[:500],
                "indexed_at":      time.time(),
            }},
        )


# Both endpoints scope to the project's connected GitHub PAT (decrypted
# from Mongo) and the project's branch. They are read-only and never
# touch the working tree on disk; everything goes through the GitHub
# REST API so no `git` binary is required.
_BROWSE_SKIP_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".cache", ".pytest_cache", ".mypy_cache",
    "coverage", ".turbo", ".vercel", ".idea", ".vscode",
}
_BROWSE_SKIP_EXTS = {
    "lock", "log", "map",
    "png", "jpg", "jpeg", "gif", "webp", "ico", "svg", "bmp",
    "mp4", "mov", "mp3", "wav", "ogg",
    "ttf", "otf", "woff", "woff2", "eot",
    "zip", "tar", "gz", "7z", "rar",
    "pdf", "exe", "dll", "so",
}
_BROWSE_MAX_FILE_BYTES = 200 * 1024  # 200 KB cap per file


def _browse_keep_path(path: str, size: int) -> bool:
    """Return True if a tree blob should appear in the browseable list."""
    if not path:
        return False
    parts = path.split("/")
    if any(p in _BROWSE_SKIP_DIRS for p in parts):
        return False
    ext = parts[-1].rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
    if ext in _BROWSE_SKIP_EXTS:
        return False
    if size and size > _BROWSE_MAX_FILE_BYTES:
        return False
    return True


def _classify_phase(step: str) -> Optional[str]:
    """Iter 168 — map a free-form step string to a coarse phase bucket
    so the live task popup can render phase chips without us having to
    touch every _log() callsite. Returns one of:
    phase_read / phase_think / phase_write / phase_verify / phase_commit
    or None if the step doesn't fit a phase (it just appears as a plain
    log line then)."""
    s = (step or "").lower()
    if any(k in s for k in (
        "📡", "📄", "reading", "fetched", "fetching",
        "cloning", "cloned", "injected", "🗂", "📋",
    )):
        return "phase_read"
    if any(k in s for k in (
        "🧠", "thinking", "plan:", "planning", "deepseek", "claude review",
    )):
        return "phase_think"
    if any(k in s for k in (
        "✏️", "💾", "writing", "regenerating", "auto-fixed", "linter",
        "validating", "sandbox",
    )):
        return "phase_write"
    if any(k in s for k in (
        "🛡", "vanguard", "verify", "verified",
    )):
        return "phase_verify"
    if any(k in s for k in (
        "🚀", "committing", "pushed", "commit", "pushing",
    )):
        return "phase_commit"
    return None


async def _log(task_id: str, step: str, status: str = "info"):
    from cto_services.db import get_db
    db = get_db()
    # Iter 168 — persist phase bucket alongside the raw step text so
    # the LiveTaskPopup can render phase chips from polled steps[].
    phase = _classify_phase(step)
    if db is not None:
        doc = {"step": step, "status": status, "ts": time.time()}
        if phase:
            doc["kind"] = phase
        await db.cto_tasks.update_one(
            {"task_id": task_id},
            {"$push": {"steps": doc}},
        )
    # Also fan out to the live SSE queue so chat bubbles can render the
    # worker tape in real time (Iter 73).  status→kind: error→fail, others→step.
    # Phase classification overrides the generic step kind when found
    # so SSE consumers can drive phase UI too.
    kind = "fail" if status == "error" else (phase or "step")
    await _emit(task_id, step, kind=kind)


async def _set_status(task_id: str, **fields):
    from cto_services.db import get_db
    db = get_db()
    if db is not None:
        # Iter 212m-12 — auto-translate failure errors into a
        # non-technical Hinglish explanation with concrete steps so
        # founders aren't staring at raw stack traces. We only run
        # the translator when the new status is `failed` AND a
        # raw error string is being set.
        if fields.get("status") == "failed" and fields.get("error"):
            # 2026-09-17 — Bug 8 fix: "33.9% task success rate" with no
            # breakdown of WHY. Gate-rejected failures (hallucination/
            # syntax/design-lint checks) return an error STRING with no
            # exception at all, so they never get an `error_code` from
            # the exception-classifier call sites (worker_git.py /
            # worker_api.py's `except Exception` blocks). Fill the gap
            # here, at the one shared write choke point, so EVERY
            # failure gets tagged — never overwrite an `error_code` a
            # caller already set (the exception-classifier path is more
            # precise than this best-effort text match).
            if not fields.get("error_code"):
                from services.failure_taxonomy import classify_failure_reason_from_text
                fields["error_code"] = classify_failure_reason_from_text(fields["error"])
            try:
                # 2026-08-28 · P0 hotfix — an INTERNAL_CALL_ERROR (a bug
                # in AUREM's own calling code, e.g. a missing-argument
                # TypeError) must NEVER go through the LLM rewrite below.
                # The raw text often contains words that happen to look
                # like user-data field names (e.g. "author_email",
                # "author_name"), and the LLM has been observed
                # confidently inventing "update your profile" guidance
                # from that — blaming the user for AUREM's own bug. Use
                # the already-correct, human-reviewed catalog message
                # instead, same one loop_engine.py's _fail_ship() uses.
                if fields.get("error_code") == "INTERNAL_CALL_ERROR":
                    from core.errors import translate_error, ErrorCode
                    catalog = translate_error(ErrorCode.INTERNAL_CALL_ERROR)
                    fields["error_plain"]      = catalog["what_happened"]
                    fields["error_steps"]      = catalog["what_to_try"]
                    fields["error_suggestion"] = ""
                    fields["error_source"]     = "internal_call_error_catalog"
                else:
                    from services.error_translator import translate
                    friendly = await translate(fields["error"])
                    fields["error_plain"]      = friendly.get("plain") or ""
                    fields["error_steps"]      = friendly.get("steps") or []
                    fields["error_suggestion"] = friendly.get("suggestion") or ""
                    fields["error_source"]     = friendly.get("source") or "unknown"
            except Exception as _e:                  # noqa: BLE001
                # Translator must never block the failure write —
                # fall back to leaving only `error` populated.
                logger.warning("error_translator wedged: %r", _e)
        # 2026-09-27 MASTER TASK Part B #6/#7 — on every TERMINAL status
        # write (done/failed), roll up this task's real LLM spend from
        # the unified `real_llm_usage` ledger (Part A) straight onto the
        # task document: `tokens_used`/`cost_usd` (so /admin/agent-tokens,
        # /admin/token-pnl and the user's own tokens-used total agree
        # with the SAME source everywhere), `llm_mode` (the mode(s) this
        # task actually ran — fixes the empty "mode distribution" the
        # founder reported), and — for failures only, and only when no
        # human-readable reason was set above — a real `error_reason`
        # pulled from the LAST failed call this task made, so failures
        # stop showing "unknown".
        if fields.get("status") in ("done", "failed"):
            try:
                await _rollup_task_llm_usage(db, task_id, fields)
            except Exception as _e:                  # noqa: BLE001
                logger.warning("[master-task Part B] llm usage rollup failed for task=%s: %r",
                                task_id, _e)
        await db.cto_tasks.update_one({"task_id": task_id}, {"$set": fields})


async def _rollup_task_llm_usage(db, task_id: str, fields: dict) -> None:
    """Aggregate every `real_llm_usage` row this task's LLM calls wrote
    (Part A's unified ledger, matched on `task_id`) and merge the
    totals into `fields` (mutated in place) before the caller's
    `update_one`. Never raises past its own try/except — see caller."""
    agg = await db.real_llm_usage.aggregate([
        {"$match": {"task_id": task_id}},
        {"$group": {
            "_id": None,
            "tokens": {"$sum": "$total_tokens"},
            "cost_usd": {"$sum": "$cost_usd"},
            "calls": {"$sum": 1},
            "modes": {"$addToSet": "$mode"},
            "models": {"$addToSet": "$model"},
        }},
    ]).to_list(1)
    if not agg:
        return
    row = agg[0]
    fields["tokens_used"] = int(row.get("tokens") or 0)
    fields["cost_usd"] = round(float(row.get("cost_usd") or 0.0), 6)
    fields["llm_call_count"] = int(row.get("calls") or 0)
    fields["llm_modes"] = [m for m in (row.get("modes") or []) if m and m != "unknown"]
    fields["llm_models"] = [m for m in (row.get("models") or []) if m]
    if fields.get("status") == "failed" and not fields.get("error") and not fields.get("error_code"):
        last_failed = await db.real_llm_usage.find_one(
            {"task_id": task_id, "status": "failed"},
            {"_id": 0, "error_reason": 1},
            sort=[("ts", -1)],
        )
        if last_failed and last_failed.get("error_reason"):
            fields["error_reason"] = last_failed["error_reason"]


def _sh(cmd: list, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def _load_design_system() -> str:
    """Load the AUREM design-system prompt once at module import. If the
    file is missing (e.g. fresh deploy), we degrade gracefully — the
    base _AI_SYS still ships."""
    try:
        import pathlib
        p = pathlib.Path(__file__).parent.parent / "prompts" / "aurem_design_system.md"
        if p.exists():
            return "\n\n# AUREM DESIGN SYSTEM — when emitting frontend code (.jsx/.tsx/.css/.html), follow EVERY rule below:\n\n" + p.read_text()
    except Exception:
        pass
    return ""


# 2026-09-12 — Bug 1 fix (critical-diff hallucination bug). Shared by
# both `worker_api.py` (GitHub-fetched files) and `worker_git.py`
# (local clone files) so the SAME safe budget + explicit "don't guess
# the part you can't see" warning applies regardless of which worker
# reads the file. See the fuller rationale on `_dropped_import_reasons`
# below — this is the input-side half of that same fix.
FILE_CONTEXT_CHAR_CAP = 40_000


def _context_with_truncation_marker(body: str, cap: int = FILE_CONTEXT_CHAR_CAP) -> tuple[str, bool]:
    """Cap `body` to `cap` chars for the AI-context prompt. Returns
    `(possibly-truncated body, was_truncated)`. When truncated, an
    explicit in-band warning is appended telling the model NOT to
    attempt a full-file rewrite of this specific file — the whole
    point of the cap is to bound prompt cost, not to make the model
    guess at content it never saw and silently delete it."""
    if len(body) <= cap:
        return body, False
    real_len = len(body)
    marker = (
        f"\n\n/* ⚠️ TRUNCATED — this file is {real_len:,} chars total; "
        f"only the first {cap:,} are shown above. DO NOT attempt a "
        f"full-file rewrite of this file — you have not seen all of "
        f"it and would delete real content you never saw. If this "
        f"file must change, say so in SUMMARY and skip its FILE "
        f"block instead of guessing. */"
    )
    return body[:cap] + marker, True


# Patterns the verifier rejects — AI sometimes sneaks placeholders past
# the prompt. We catch them client-side BEFORE pushing to GitHub so the
# user never sees a commit that silently truncates their file.
_TRUNCATION_PATTERNS = [
    "... rest of file",
    "... existing code",
    "... unchanged",
    "...(truncated)",
    "... (truncated)",
    "// rest of file",
    "/* existing code */",
    "/* ... */",
    "# ... existing",
    "# rest of file",
    "<keep the rest",
    "<rest of file",
    "<existing code",
    "[rest of file",
    "[existing code",
    "// keep existing",
    "// ... (",
    "/* TODO: keep",
]


def _looks_truncated(path: str, body: str) -> Optional[str]:
    """Return a human reason if `body` looks like an AI-truncated edit,
    else None. Run on every FILE block before we push."""
    if not body or not body.strip():
        return "empty file body"
    low = body.lower()
    for pat in _TRUNCATION_PATTERNS:
        if pat.lower() in low:
            return f"contains placeholder '{pat}'"
    # Very short edits to non-trivial files are suspicious too — but we
    # only flag them when the body has fewer than 3 non-blank lines AND
    # the extension suggests code (not config/markdown).
    non_blank = sum(1 for ln in body.splitlines() if ln.strip())
    is_codey = path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"))
    if is_codey and non_blank < 3:
        return f"only {non_blank} non-blank lines for a code file"
    return None


# Iter 36: bulletproof retry wrapper for transient upstream failures.
# Wraps an async coroutine factory in exponential-backoff retry. Every
# failed attempt is logged to the task feed so the user sees WHAT went
# wrong, not just a silent "task failed". This is what makes Ship via
# CTO self-heal on rate-limit / 5xx / network blips instead of giving up.
async def _retry(coro_factory, *, what: str, task_id: str,
                 attempts: int = 3, base_sleep: float = 1.5):
    """Run `coro_factory()` up to `attempts` times with exp backoff
    (1.5s → 3s → 6s …). Re-raises the LAST exception if every attempt fails."""
    last_exc: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            # 2026-08-25 — never surface the raw exception string in
            # the live worker tape (see root-cause note on the outer
            # handlers below). A fast, non-LLM classification is
            # enough for a mid-flight retry warning.
            from services.error_classifier import classify_error
            _safe_msg = classify_error(e)["user_message"]
            await _log(
                task_id,
                f"⏳ {what} failed (attempt {i}/{attempts}): {_safe_msg}",
                "warning",
            )
            if i < attempts:
                await asyncio.sleep(base_sleep * (2 ** (i - 1)))
    assert last_exc is not None
    raise last_exc


def _hallucination_reasons(blocks: dict, originals: dict) -> list[str]:
    """Iter 212m-177 — P0-4a. Flag proposed full-file rewrites that keep
    almost none of the REAL file's lines (hallucinated content).

    2026-09-12 — content-fidelity fix. This previously SKIPPED the
    check entirely for any original file with fewer than 10 non-blank
    lines (`if len(_olines) < 10: continue`) — almost certainly meant
    only to dodge a division-by-zero on empty files, but it silently
    exempted every SMALL file (a short README.md, a small config,
    etc.) from the fidelity check. Confirmed live: a task that asked
    to append exactly one line to a small README.md instead got the
    entire file replaced with generic "Project Name / Installation"
    boilerplate — 0% of the real content preserved — and this gate
    never fired because the original had under 10 lines. The
    kept-line-ratio math is correct down to a single original line
    (e.g. 1 line kept out of 1 = ratio 1.0; 0 kept out of 1 = ratio
    0.0 — no divide-by-zero unless the file is TRULY empty), so the
    only case that actually needs to skip is a genuinely empty
    original (nothing to preserve, nothing to divide by)."""
    out: list[str] = []
    for _p, _new in blocks.items():
        _orig = originals.get(_p) or originals.get(_p.lstrip("./"))
        if not _orig:
            continue          # brand-new file — allowed
        _olines = [l.strip() for l in _orig.splitlines() if l.strip()]
        if len(_olines) == 0:
            continue          # original is genuinely empty — nothing to preserve
        _nset = {l.strip() for l in (_new or "").splitlines() if l.strip()}
        _kept = sum(1 for l in _olines if l in _nset)
        _ratio = _kept / len(_olines)
        if _ratio < 0.4:
            out.append(
                f"{_p} — proposed rewrite keeps only "
                f"{int(_ratio * 100)}% of the real file's lines "
                f"(likely hallucinated content)")
    return out


# 2026-09-12 — Bug 1 fix (critical-diff hallucination bug). The
# keep-ratio gate above ONLY catches WHOLESALE rewrites (>60% of the
# file replaced). Confirmed live: a trivial "update the footer text"
# request on a real App.js came back with 4 context-provider imports
# and the top-level React import silently deleted, while ~90%+ of the
# rest of the file was preserved verbatim — a keep-ratio around 0.85,
# nowhere near the 0.4 trip-wire, so the existing gate never fired and
# the diff (which would have broken auth/cart/content/translations
# across the whole site) reached the "Approve the fix" button marked
# safe. Root cause: a SURGICAL-looking edit can still delete a
# structurally load-bearing line (a top-level import/require) while
# leaving the bulk of the file untouched — that class of damage is
# invisible to a percentage-of-lines metric. This check is independent
# of the ratio above and deterministic (regex, zero LLM cost): any
# top-level import/require/from-import statement present in the
# ORIGINAL file is REQUIRED to still be present (verbatim, whitespace-
# insensitive) in the new content, for every language we support
# editing. A single missing import is treated exactly like a
# hallucination — same retry-with-real-file-reinjection path.
_JS_IMPORT_RX = re.compile(
    r"^\s*(?:import\b[^;\n]*?(?:from\s+['\"][^'\"]+['\"])?"
    r"|const\s+[\w${},\s]+=\s*require\(['\"][^'\"]+['\"]\))\s*;?\s*$",
    re.MULTILINE,
)
_PY_IMPORT_RX = re.compile(
    r"^\s*(?:import\s+[\w.]+(?:\s+as\s+\w+)?"
    r"|from\s+[\w.]+\s+import\s+.+)\s*$",
    re.MULTILINE,
)


def _extract_top_level_imports(path: str, body: str) -> list[str]:
    """Every top-level import/require statement in `body`, normalised
    (whitespace-collapsed) so a purely-cosmetic reformat (extra
    spaces, trailing semicolon) doesn't false-positive as "removed"."""
    if not body:
        return []
    if path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        rx = _JS_IMPORT_RX
    elif path.endswith(".py"):
        rx = _PY_IMPORT_RX
    else:
        return []
    out = []
    for m in rx.finditer(body):
        norm = re.sub(r"\s+", " ", m.group(0)).strip().rstrip(";")
        if norm:
            out.append(norm)
    return out


def _dropped_import_reasons(blocks: dict, originals: dict) -> list[str]:
    """Return a human reason per file where the new content is missing
    ANY top-level import/require the original file had. Skips files
    truncated on read (see `contents[path] = body[:N]` in the worker)
    — those are already flagged separately by the truncation-aware
    prompt note, and penalising a model for not reproducing content it
    was never shown would be unfair and noisy."""
    out: list[str] = []
    for _p, _new in blocks.items():
        _orig = originals.get(_p) or originals.get(_p.lstrip("./"))
        if not _orig:
            continue                      # brand-new file — allowed
        _orig_imports = _extract_top_level_imports(_p, _orig)
        if not _orig_imports:
            continue                      # nothing importy to preserve
        _new_norm = re.sub(r"\s+", " ", _new or "")
        _missing = [
            imp for imp in _orig_imports
            if imp not in _new_norm
        ]
        if _missing:
            out.append(
                f"{_p} — dropped {len(_missing)} import/require "
                f"statement(s) present in the real file: "
                + "; ".join(_missing[:4])
                + (f" (+{len(_missing) - 4} more)" if len(_missing) > 4 else "")
            )
    return out


# 2026-09-17 — Bug 2 fix (live incident, commit 36ecdee): a task
# asking ONLY to "add a regression test comment" also silently
# deleted two unrelated `console.log(...)` statements elsewhere in
# the same file — verified against the real commit. Neither
# `_dropped_import_reasons` above (imports only) nor
# `_symbol_scope_creep_reasons` below (named top-level function/class/
# const bodies only) catches a bare statement — not an import, not
# inside a rewritten named symbol — silently vanishing.
#
# Deliberately scoped NARROW to avoid false-positiving on legitimate
# multi-line edits (a real "fix computeTotal()'s rounding" task
# naturally removes some call lines as part of the fix — flagging
# that would be noise, not safety). This only activates when the
# task's OWN language claims to be a trivial, purely-additive change
# ("add a comment/marker/note/flag") — the exact shape of the
# incident. For every other kind of task this check stays off
# entirely; `_symbol_scope_creep_reasons` already covers deletions
# inside named symbols for the general case.
_INSERT_ONLY_INTENT_RX = re.compile(
    r"\b(add|insert|leave|plant|drop|write|place)\b"
    r"(?:(?!\.).){0,40}\b"
    r"(comment|marker|note|flag)\b", re.I,
)
_REMOVAL_INTENT_RX = re.compile(
    r"\b(remove|delete|drop|clean\s*up|refactor|simplify|consolidate|"
    r"strip\s+out|get\s+rid\s+of)\b", re.I,
)
# A "notable" statement is real code — a call expression or a return
# — not punctuation/braces/blank/comment noise, so cosmetic
# reformatting (a brace moving to its own line) never trips this.
_NOTABLE_STATEMENT_RX = re.compile(
    r"[A-Za-z_$][\w.$]*\s*\([^)]*\)\s*;?\s*$"     # call expression: foo(...);
    r"|^return\b.+;?\s*$"                          # a return statement
)


def _dropped_statement_reasons(task: str, blocks: dict, originals: dict) -> list[str]:
    """Return a human reason per file where a real, notable statement
    present in the original silently disappeared in the rewrite, on
    a task that itself claims to be a trivial add-a-comment/marker
    change (see module comment above for why the trigger is scoped
    this narrow). Language-agnostic (line-based, like
    `_dropped_import_reasons`) — same retry-then-abort path."""
    if not _INSERT_ONLY_INTENT_RX.search(task or ""):
        return []                       # not an "add a marker"-shaped task
    if _REMOVAL_INTENT_RX.search(task or ""):
        return []                       # explicit removal intent — allowed
    out: list[str] = []
    for _p, _new in blocks.items():
        _orig = originals.get(_p) or originals.get(_p.lstrip("./"))
        if not _orig:
            continue                    # brand-new file — nothing to drop
        _new_norm = re.sub(r"\s+", " ", _new or "")
        _dropped = []
        for _line in _orig.splitlines():
            _stripped = _line.strip()
            if len(_stripped) < 8:      # too short to be meaningful
                continue
            if _stripped.startswith(("//", "#", "*", "/*")):
                continue                 # comments — not our concern here
            if not _NOTABLE_STATEMENT_RX.search(_stripped):
                continue
            _norm_line = re.sub(r"\s+", " ", _stripped)
            if _norm_line not in _new_norm:
                _dropped.append(_stripped[:120])
        if _dropped:
            out.append(
                f"{_p} — {len(_dropped)} existing statement(s) silently "
                f"removed on a task that only asked to add a comment/"
                f"marker: " + "; ".join(_dropped[:3])
                + (f" (+{len(_dropped) - 3} more)" if len(_dropped) > 3 else "")
            )
    return out


# 2026-09-13 — Phase 1 scope-creep broadening. The two gates above
# (`_hallucination_reasons`, `_dropped_import_reasons`) only ever
# looked at whole-file line-preservation or the import block. Neither
# catches a THIRD failure mode confirmed possible in the Phase 0 live
# adversarial rerun of this same incident: a model that keeps every
# import intact and keeps the overall keep-ratio high, but silently
# rewrites (or deletes) an EXISTING top-level function/class/const it
# was never asked to touch — i.e. same-file, different-symbol scope
# creep. A "fix the footer text" task that also quietly changes
# `formatPrice()`'s rounding logic three functions down would sail
# through both prior gates untouched. This is a deterministic,
# name-based check (no LLM cost): every top-level symbol present in
# the ORIGINAL file is extracted by name + full source span; if that
# same symbol exists in the new content but its body is substantially
# different (or it's gone entirely), and the symbol's own name is
# never mentioned anywhere in the task text, it's flagged exactly like
# a dropped import — same retry-with-real-file-reinjection path.
_JS_TOP_SYMBOL_RX = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\("
    r"|^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
    r"|^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?function\b"
    r"|^(?:export\s+)?class\s+(\w+)\b",
    re.MULTILINE,
)
_PY_TOP_SYMBOL_RX = re.compile(
    r"^(?:async\s+)?def\s+(\w+)\s*\("
    r"|^class\s+(\w+)\b",
    re.MULTILINE,
)


def _extract_top_level_symbols(path: str, body: str) -> dict[str, str]:
    """Best-effort {symbol_name: full_source_span} for every top-level
    function/class/const-arrow-fn in `body`. Brace-balanced for
    JS/TS, indentation-balanced for Python — good enough to diff a
    symbol's ACTUAL body across two file versions, not just detect
    its name exists somewhere. Returns {} for unsupported extensions
    or files with no matches (e.g. pure config/markdown)."""
    if not body:
        return {}
    out: dict[str, str] = {}
    if path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        for m in _JS_TOP_SYMBOL_RX.finditer(body):
            name = next((g for g in m.groups() if g), None)
            if not name or name in out:
                continue
            brace_start = body.find("{", m.end() - 1)
            if brace_start == -1:
                continue
            depth = 0
            end = None
            for i in range(brace_start, len(body)):
                if body[i] == "{":
                    depth += 1
                elif body[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end is not None:
                out[name] = body[m.start():end]
    elif path.endswith(".py"):
        lines = body.splitlines()
        for m in _PY_TOP_SYMBOL_RX.finditer(body):
            name = next((g for g in m.groups() if g), None)
            if not name or name in out:
                continue
            start_line_idx = body.count("\n", 0, m.start())
            base_indent = len(lines[start_line_idx]) - len(lines[start_line_idx].lstrip())
            span_lines = [lines[start_line_idx]]
            for ln in lines[start_line_idx + 1:]:
                if not ln.strip():
                    span_lines.append(ln)
                    continue
                indent = len(ln) - len(ln.lstrip())
                if indent > base_indent:
                    span_lines.append(ln)
                else:
                    break
            out[name] = "\n".join(span_lines)
    return out


def _norm_span(src: str) -> str:
    return re.sub(r"\s+", " ", src or "").strip()


_SCOPE_CREEP_STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is",
    "it", "this", "that", "with", "as", "update", "updates", "change",
    "changes", "fix", "add", "adds", "remove", "make", "makes", "set",
    "sets", "say", "text", "file", "please", "your", "task", "code",
}


def _task_keywords(task: str) -> list[str]:
    """Lowercased content words (>=4 chars, stopwords stripped) from
    the task text — used as a looser "plausibly related to the task"
    signal than a literal symbol-name match (a task about "the footer
    copyright text" is legitimately allowed to touch whichever
    top-level component happens to render that footer, even though
    the task never says the component's name)."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{3,}", task or "")
    return [w.lower() for w in words if w.lower() not in _SCOPE_CREEP_STOPWORDS]


def _symbol_scope_creep_reasons(task: str, blocks: dict, originals: dict) -> list[str]:
    """Flag any EXISTING top-level symbol (function/class/const-fn)
    that got substantially rewritten or deleted, where the symbol is
    NOT plausibly related to the task — same-file-different-symbol
    scope creep, independent of the import gate above.

    "Plausibly related" = the task names the symbol directly, OR any
    task content-word already appears in that symbol's own source
    (before or after the edit) — e.g. a task about "the footer
    copyright text" is allowed to touch whichever component's source
    actually contains the word "footer", without having to name that
    component. This avoids false-positiving on the single most common
    real edit shape (content change inside the component that renders
    it) while still catching an unrelated symbol (e.g. a pricing
    helper) whose source has no connection to the task at all."""
    task_low = (task or "").lower()
    kws = _task_keywords(task)
    out: list[str] = []
    for _p, _new in blocks.items():
        _orig = originals.get(_p) or originals.get(_p.lstrip("./"))
        if not _orig:
            continue                      # brand-new file — allowed
        orig_syms = _extract_top_level_symbols(_p, _orig)
        if not orig_syms:
            continue
        new_syms = _extract_top_level_symbols(_p, _new or "")
        for name, orig_src in orig_syms.items():
            new_src = new_syms.get(name)
            combined_low = (orig_src + " " + (new_src or "")).lower()
            plausibly_related = (
                name.lower() in task_low
                or any(kw in combined_low for kw in kws)
            )
            if plausibly_related:
                continue
            if new_src is None:
                out.append(
                    f"{_p} — top-level symbol `{name}` was REMOVED "
                    f"even though the task never mentions it")
                continue
            if _norm_span(new_src) != _norm_span(orig_src):
                import difflib as _difflib
                ratio = _difflib.SequenceMatcher(
                    None, _norm_span(orig_src), _norm_span(new_src),
                ).ratio()
                if ratio < 0.9:
                    out.append(
                        f"{_p} — top-level symbol `{name}`'s body was "
                        f"substantially changed ({int(ratio * 100)}% "
                        f"similar) even though it has no connection to "
                        f"the task — likely unrelated behavior change")
    return out


def _file_scope_creep_reasons(task: str, blocks: dict, originals: dict) -> list[str]:
    """Flag any file in the proposed edit that was NEVER shown to the
    model (not in `originals`) AND is never named (path or basename)
    in the task text — a genuinely new/unrelated file the model
    invented on its own, as opposed to a new file the task actually
    asked to create."""
    task_low = (task or "").lower()
    known = {p for p in originals} | {p.lstrip("./") for p in originals}
    out: list[str] = []
    for _p in blocks:
        if _p in known or _p.lstrip("./") in known:
            continue
        basename = _p.rsplit("/", 1)[-1].lower()
        if basename and basename in task_low:
            continue
        if _p.lower() in task_low:
            continue
        out.append(
            f"{_p} — new file created but never shown to the model "
            f"NOR named anywhere in the task (possible unrelated "
            f"scope creep)")
    return out


# 2026-09-12 — content-fidelity fix, round 2. Confirmed live (commit
# 2e74ea1, founder repro "verify-3"): the round-1 keep-ratio gate above
# only catches WHOLESALE rewrites. It does nothing for the opposite,
# equally serious failure — the original content is fully preserved
# (ratio ~1.0, gate never fires) but the ONE specific literal string
# the task explicitly required (e.g. "append exactly `<!-- marker -->`
# as a new line") comes out BLANK or altered in the actual edit. The
# commit message and the pipeline's own post-push verify both looked
# "correct" because they only ever compared what-we-pushed against
# what-GitHub-now-serves — never against what the task actually asked
# for. This check closes that specific gap: when a task names an
# EXACT literal (a deliberate, unambiguous instruction pattern —
# "exactly `X`" / "exactly \"X\""), the generated edit(s) must contain
# that literal verbatim, or it's treated the same as a hallucination.
_VERBATIM_LITERAL_RE = re.compile(
    r"exactly[\s:]*[\-—]?\s*(?P<q>[`\"'])(?P<lit>.+?)(?P=q)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_verbatim_literals(task: str) -> list[str]:
    """Every quoted literal the task explicitly said to use "exactly"."""
    if not task:
        return []
    return [
        m.group("lit") for m in _VERBATIM_LITERAL_RE.finditer(task)
        if m.group("lit").strip()
    ]


def _verbatim_reasons(task: str, blocks: dict) -> list[str]:
    """Flag any "exactly `X`"-style literal from the task that is
    missing from every edited file's new content — the actual write
    didn't do what was explicitly, unambiguously asked."""
    literals = _extract_verbatim_literals(task)
    if not literals or not blocks:
        return []
    haystack = "\n".join(blocks.values())
    out: list[str] = []
    for lit in literals:
        if lit not in haystack:
            out.append(
                f'task required the EXACT literal "{lit[:80]}" but it is '
                f"missing from the generated edit(s) — likely written "
                f"blank or altered")
    return out
