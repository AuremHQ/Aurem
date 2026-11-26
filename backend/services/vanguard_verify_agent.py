"""
services/vanguard_verify_agent.py
=================================

Iter 111 — Separate Vanguard verify agent (Anthropic-style "defending-
code-reference-harness"):

> ORA writes code → handed off to a SEPARATE agent (different prompt,
> different model) → re-reviews the patch for the 25 known vulnerability
> patterns PLUS its own LLM-grade judgement → only on PASS does the
> patch progress to commit.

Iter 169 — migrated to OpenRouter (anthropic/claude-sonnet-4.5).
The previous Emergent SDK dependency was dead weight after llm.py was
cleaned up — when EMERGENT_LLM_KEY was unset the verify pipeline
silently skipped LLM review, leaving only the regex floor. Now it
goes through the same OPENROUTER_API_KEY all other LLM calls use, so
the second-agent review actually runs in production.

Iter 212m-41 — soften the LLM blocking rule. The previous prompt told
the agent `pass` was false on ANY CRITICAL or HIGH finding, which made
Claude (now overly diligent) block routine commits on theoretical
HIGH risks (e.g. `localStorage.setItem("token", …)`, inline `style=`,
React `dangerouslySetInnerHTML` in a tooltip, etc.). The regex floor
(`scan_file_blocks`) is what guarantees the real CRITICAL gates; the
LLM agent is now advisory for HIGH and only blocks on CRITICAL. Two
new env vars provide escape hatches:

  VANGUARD_VERIFY_BLOCK_LEVEL = CRITICAL | HIGH | OFF   (default CRITICAL)
  VANGUARD_VERIFY_ENABLED     = 1/0                     (default 1)

Public API
----------
    await verify_patch(file_blocks, repo_ctx=...) -> {
        "pass":     bool,
        "findings": [ {file, line, severity, rule, message} ],
        "summary":  str,
        "model":    str,
    }
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
from typing import Optional

from .vanguard_scanner import scan_file_blocks, has_critical

logger = logging.getLogger(__name__)

# Separate-agent isolation: this is a DIFFERENT model/persona than ORA.
# Claude Sonnet 4.5 via OpenRouter — same key as the rest of the app.
# Iter 212g — use OpenRouter's dotted ID (4.5); the dash-date Anthropic
# native format returns HTTP 400 from OpenRouter.
#
# SSOT-refactor (Feb 2026) — Claude/DeepSeek model IDs now imported
# from `services.llm.openrouter_providers` so this file cannot drift
# from Parliament V2's canonical model slugs. Env override still wins
# for PROD hot-swap without a redeploy.
try:
    from .llm.openrouter_providers import _CLAUDE_MODEL as _SSOT_CLAUDE
    from .llm import _deepseek_model as _ssot_deepseek_model
    _SSOT_DEEPSEEK = _ssot_deepseek_model()
except Exception as _e:  # pragma: no cover — defensive on import cycles
    logger.warning("vanguard_verify_agent: SSOT model import failed (%r)", _e)
    _SSOT_CLAUDE = "anthropic/claude-sonnet-4.5"
    _SSOT_DEEPSEEK = "deepseek/deepseek-chat"

_VERIFY_MODEL = os.environ.get(
    "VANGUARD_VERIFY_MODEL",
    _SSOT_CLAUDE,
)

# Iter 212m-172 — Rescue model for when the primary Vanguard verifier
# fails (timeout, 5xx, empty response, invalid JSON).  DeepSeek is the
# same fallback used across the codebase (services/llm.py::_deepseek_model)
# and is env-tunable so PROD can pin an alternative on outage.
_VERIFY_RESCUE_MODEL = os.environ.get(
    "VANGUARD_VERIFY_RESCUE_MODEL",
    _SSOT_DEEPSEEK,
)

# Iter 212m-41 — env-tunable severity threshold + kill-switch.
_BLOCK_LEVEL = (
    os.environ.get("VANGUARD_VERIFY_BLOCK_LEVEL", "CRITICAL") or "CRITICAL"
).upper()
_ENABLED = (
    os.environ.get("VANGUARD_VERIFY_ENABLED", "1").lower()
    in ("1", "true", "yes", "on")
)


def _severity_blocks(severity: str) -> bool:
    """Should this finding actually block the commit?
    Honours VANGUARD_VERIFY_BLOCK_LEVEL.

      OFF       → never blocks
      CRITICAL  → only CRITICAL findings block
      HIGH      → CRITICAL or HIGH findings block
    """
    sev = (severity or "").upper()
    if _BLOCK_LEVEL == "OFF":
        return False
    if _BLOCK_LEVEL == "HIGH":
        return sev in ("CRITICAL", "HIGH")
    return sev == "CRITICAL"


# ──────────────────────────────────────────────────────────────────
# Iter 212m-132 — Diff-based scanning.
# ──────────────────────────────────────────────────────────────────
# THE PROBLEM (founder report):
#   Vanguard scans the ENTIRE file when reviewing a Loop patch, which
#   means pre-existing vulns in lines the patch didn't touch end up
#   blocking the commit. Result: Loop commits get blocked by issues
#   the user never asked us to introduce.
#
# THE FIX:
#   `changed_lines_for_file(base, new)` returns the 1-indexed set of
#   line numbers in the NEW content that differ from the base.
#   `filter_findings_to_changed_lines(findings, changed_lines_map)`
#   then drops any regex/LLM finding whose `line` is NOT in the
#   changed set. The whole pipeline keeps working unchanged when
#   `base_blocks` is None / missing (legacy callers, brand-new
#   files) — those scans stay full-file.
#
# Why line-set based instead of unified-diff:
#   • A unified-diff envelope would force the LLM to count hunk
#     offsets and lose continuity (model hallucinates line numbers).
#   • Sending the whole new file + the line-set as a JSON sidebar
#     keeps the LLM's review prompt close to what it sees today
#     (less prompt-engineering risk).
#   • Regex scanner already returns `line` per finding — we just
#     filter post-hoc; cheaper than rewriting `scan_file_blocks`.
# ──────────────────────────────────────────────────────────────────


def changed_lines_for_file(base: str, new: str) -> set[int]:
    """Return the 1-indexed set of line numbers in `new` that were
    added or modified relative to `base`.

    Uses `difflib.SequenceMatcher` opcodes:
      • "equal"   → unchanged, skip
      • "insert"  → all lines j1..j2 in new are NEW
      • "replace" → all lines j1..j2 in new replaced something in base

    `delete` opcodes don't contribute to "new lines" — they only
    removed something from base (we can't introduce a vuln by
    deleting lines).
    """
    if not new:
        return set()
    if not base:
        # New file — every non-empty line is "changed".
        return {i + 1 for i, ln in enumerate(new.splitlines())}
    base_lines = base.splitlines()
    new_lines  = new.splitlines()
    sm = difflib.SequenceMatcher(a=base_lines, b=new_lines,
                                 autojunk=False)
    changed: set[int] = set()
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        if op in ("insert", "replace"):
            # j is 0-indexed into new_lines; we want 1-indexed line nums.
            for j in range(j1, j2):
                changed.add(j + 1)
    return changed


def changed_lines_map(base_blocks: dict[str, str],
                      new_blocks:  dict[str, str]) -> dict[str, set[int]]:
    """For each `path` in `new_blocks`, compute the changed-line set
    relative to `base_blocks[path]` (or empty base if missing —
    treated as a brand-new file).  Returns `{path: {line_nums}}`."""
    out: dict[str, set[int]] = {}
    for path, new_content in (new_blocks or {}).items():
        base = (base_blocks or {}).get(path, "") or ""
        out[path] = changed_lines_for_file(base, new_content or "")
    return out


def filter_findings_to_changed_lines(
    findings: list[dict],
    line_map: dict[str, set[int]],
) -> tuple[list[dict], list[dict]]:
    """Split `findings` into (kept_findings, dropped_findings).

    A finding is KEPT when EITHER:
      • its `file` is not in `line_map` (we don't have diff info →
        be safe, keep it), OR
      • its `line` is missing/0 (scanner didn't tell us where → keep
        so we don't silently drop a real vuln), OR
      • its `line` is in the changed set for that file.

    DROPPED findings are returned for audit/logging so we can show
    "X pre-existing issues skipped" in the verify summary.
    """
    kept:    list[dict] = []
    dropped: list[dict] = []
    for f in findings or []:
        path = f.get("file") or f.get("path") or ""
        line = f.get("line")
        if not path or path not in line_map:
            kept.append(f)
            continue
        if not line:  # 0 or None → keep (scanner couldn't pinpoint)
            kept.append(f)
            continue
        try:
            ln_int = int(line)
        except (TypeError, ValueError):
            kept.append(f)
            continue
        if ln_int in line_map[path]:
            kept.append(f)
        else:
            dropped.append({**f, "_skipped_reason": "pre_existing"})
    return kept, dropped


_VERIFY_SYSTEM = """You are the **Vanguard Verify Agent** — a dedicated security
reviewer that re-audits code patches BEFORE they are committed. You are
NOT the same agent that wrote the code. Your sole job is to find
vulnerabilities, dangerous patterns, and architectural risks that the
regex pre-scan may have missed.

Review the patch for ALL of these dimensions:
  1. Secrets / credentials / tokens / keys (any hard-coded value)
  2. Code injection — eval, exec, shell=True, dynamic imports, sql
     concatenation, template injection, untrusted deserialisation
  3. Path traversal / arbitrary file read or write
  4. Server-side request forgery (urlopen / requests on user-controlled URL)
  5. Cross-site scripting — innerHTML, dangerouslySetInnerHTML, raw
     html.unescape on user input, jQuery .html() with user data
  6. Insecure crypto — md5, sha1, custom RNG, hardcoded IV, ECB mode
  7. Hardcoded admin / debug paths or auth bypasses
  8. Open CORS (allow_origins=['*'] in production)
  9. Logic bombs — sleep(), while True with no break, recursion without
     base case
 10. Missing authorization / authentication on a sensitive route
 11. Direct SQL with user-controlled fragments
 12. Race conditions — async without lock, non-atomic check-then-write

SEVERITY RULES (Iter 212m-41 — production calibration):
  CRITICAL = an attacker can OWN the system or read other users' data
             RIGHT NOW with this exact patch deployed. Examples:
             real hard-coded API key, eval(user_input), SQL string
             concatenation on a user-controlled fragment, an unauth'd
             admin route.  Be SURE before you mark anything critical.
  HIGH     = real risk but needs preconditions (e.g. user already
             authenticated, internal-only endpoint, defence-in-depth).
             Examples: localStorage of a short-lived JWT,
             dangerouslySetInnerHTML of a static literal string.
  MEDIUM/LOW = code-smell, style, or theoretical risk.

DO NOT MARK CRITICAL/HIGH for:
  • Inline CSS via React's `style={{}}` prop (this is not XSS).
  • Routine `localStorage.setItem` / `getItem` for app state.
  • `console.log` / `console.error` in dev paths.
  • UI styling, JSX class names, refactors that don't touch auth or IO.
  • Adding a UI banner / pill / button / copy change.
  • Reusing an existing pattern that already exists elsewhere in the
    repo — if the pattern is widespread, the verdict is INFO at most.

You MUST respond with VALID JSON only, no prose:
{
  "pass": true | false,
  "findings": [
    {"file": "...", "line": 42, "severity": "CRITICAL|HIGH|MEDIUM|LOW",
     "rule": "short-slug", "message": "1-line explanation"}
  ],
  "summary": "1-paragraph executive summary"
}

Set `pass` to FALSE ONLY when there is at least one finding you
genuinely believe is CRITICAL by the rules above. Empty findings,
all MEDIUM/LOW, or HIGH-only findings → set `pass` to TRUE.
"""


def _has_executable_python(blocks: dict) -> bool:
    """True iff any .py file in the patch defines a function or class.
    Used to decide whether the E2B smoke-import gate is worth running."""
    for path, content in (blocks or {}).items():
        if not path.endswith(".py"):
            continue
        if not content:
            continue
        for ln in content.splitlines():
            s = ln.strip()
            if s.startswith("def ") or s.startswith("async def ") or s.startswith("class "):
                return True
    return False


def _changed_lines_body(lines: list[str], changed: set[int]) -> tuple[str, set[int]]:
    """Build the review body for ONE file in diff mode from the CHANGED
    lines themselves (±3 lines of context each) instead of head/tail
    truncation. A vuln on a changed line in the middle of a 600-line
    file must be VISIBLE to the reviewer — the old first/last-250
    cut hid exactly those lines while the CHANGED_LINES header still
    told the model to review them.

    Returns (body, visible) where `visible` is the subset of `changed`
    actually present in the body, so the caller can list ONLY those
    lines in the CHANGED_LINES header — never a line that was cut
    away. Lines are prefixed with their 1-indexed number so the
    model can cite them. When the changed region is huge (>600 kept
    lines) we keep the first/last 300 kept lines and `visible`
    shrinks accordingly (still changed-lines-only)."""
    keep: set[int] = set()
    for ln in changed:
        lo = max(1, ln - 3)
        hi = min(len(lines), ln + 3)
        keep.update(range(lo, hi + 1))
    if not keep:
        return "", set()
    ordered = sorted(keep)
    if len(ordered) > 600:
        kept = sorted(set(ordered[:300]) | set(ordered[-300:]))
    else:
        kept = ordered
    kept_set = set(kept)
    visible = {ln for ln in changed if ln in kept_set}
    parts: list[str] = []
    buf = [f"{kept[0]:6d}| {lines[kept[0] - 1]}"]
    prev = kept[0]
    for ln in kept[1:]:
        if ln == prev + 1:
            buf.append(f"{ln:6d}| {lines[ln - 1]}")
        else:
            parts.append("\n".join(buf))
            parts.append(f"# … ({ln - prev - 1} unchanged lines omitted) …")
            buf = [f"{ln:6d}| {lines[ln - 1]}"]
        prev = ln
    parts.append("\n".join(buf))
    return "\n".join(parts), visible


def _repair_python_literals(text: str) -> str:
    """Replace Python-style literals (True/False/None) with their JSON
    equivalents ONLY outside string literals. A global str.replace
    corrupts finding text ("NoneType" → "nullType"); this scanner
    tracks in-string state (with backslash escapes) and leaves
    string contents byte-identical."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str: str | None = None
    while i < n:
        ch = text[i]
        if in_str is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("\"", "'"):
            in_str = ch
            out.append(ch)
            i += 1
            continue
        for py, js in (("True", "true"), ("False", "false"), ("None", "null")):
            if text.startswith(py, i):
                before = text[i - 1] if i > 0 else ""
                after = text[i + len(py)] if i + len(py) < n else ""
                if not (before.isalnum() or before == "_") and not (
                    after.isalnum() or after == "_"
                ):
                    out.append(js)
                    i += len(py)
                    break
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_review_json(text: str) -> dict | None:
    """Parse the verify-agent's JSON reply. Returns the dict, or None
    when nothing parseable exists (caller fails closed). Tries, in
    order: strict JSON → scoped Python-literal repair → {…} brace
    extraction (strict, then repaired). Never raises."""
    import re

    candidates = [text, _repair_python_literals(text)]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group(0))
        candidates.append(_repair_python_literals(m.group(0)))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


async def _llm_review(file_blocks: dict, repo_ctx: str,
                     line_map: Optional[dict[str, set[int]]] = None) -> dict:
    """Make the second-agent LLM call. Iter 169 — uses OpenRouter
    (anthropic/claude-sonnet-4.5). Returns
    {pass, findings, summary, model}. On LLM/infra error, returns
    pass=True with a note so we don't accidentally block the pipeline
    on transient infra; the regex scan stays the floor. On
    UNPARSEABLE output (2026-11-25 audit Part IX (b)) it fails
    CLOSED (pass=False + CRITICAL finding) — "the reviewer produced
    no usable verdict" is not transient infra.

    Iter 212m-132 — When `line_map` is provided (diff-aware mode),
    we annotate the patch envelope so the LLM understands which
    line numbers are NEW vs PRE-EXISTING. We also instruct it via
    the system prompt to ONLY flag issues introduced by this patch.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return {"pass": True, "findings": [],
                "summary": "verify-agent skipped (no OPENROUTER_API_KEY)",
                "model": ""}

    # Build a compact patch envelope. Each file capped so we don't blow
    # context on huge edits — first/last 250 lines is plenty for review.
    envelope_parts: list[str] = [f"# Repo: {repo_ctx}", ""]
    diff_mode_active = line_map is not None and any(
        line_map.get(p) for p in (file_blocks or {})
    )
    if diff_mode_active:
        envelope_parts.append(
            "# DIFF MODE: For each file below, `CHANGED_LINES` lists "
            "the 1-indexed line numbers in the new content that this "
            "patch ADDED or MODIFIED. Pre-existing lines are shown "
            "for context only; do NOT flag vulnerabilities on lines "
            "outside CHANGED_LINES.\n"
        )
    for path, content in (file_blocks or {}).items():
        lines = (content or "").split("\n")
        # 2026-11-25 audit Part IX (a): in diff mode the body is built
        # from the CHANGED lines (±3 context) — never head/tail
        # truncation, which hid changed middle lines while the header
        # still listed them as in-scope. `visible_changed` is the
        # subset actually present, so the header below cannot list a
        # line that was cut away.
        visible_changed: set[int] = set()
        changed_set = set(line_map.get(path) or set()) if diff_mode_active else set()
        if diff_mode_active and changed_set:
            body, visible_changed = _changed_lines_body(lines, changed_set)
            if not body:
                body = content or ""
        elif len(lines) > 500:
            head = "\n".join(lines[:250])
            tail = "\n".join(lines[-250:])
            body = head + "\n# … (truncated middle) …\n" + tail
        else:
            body = content or ""
        header = f"### {path}"
        if diff_mode_active:
            ln_set = sorted(visible_changed)
            # Compress consecutive runs for readability:
            # [1,2,3,7,8] -> "1-3, 7-8"
            runs: list[str] = []
            if ln_set:
                start = prev = ln_set[0]
                for x in ln_set[1:]:
                    if x == prev + 1:
                        prev = x
                        continue
                    runs.append(f"{start}-{prev}" if start != prev else f"{start}")
                    start = prev = x
                runs.append(f"{start}-{prev}" if start != prev else f"{start}")
            header += f"\nCHANGED_LINES: {', '.join(runs) or '(none — file unchanged?)'}"
        envelope_parts.append(f"{header}\n```\n{body}\n```\n")
    envelope = "\n".join(envelope_parts)[:48_000]  # ~12k tokens of patch

    try:
        from .llm import call_openrouter_model
        # Iter 111 — hard 30s ceiling so the verify-agent can never
        # add unbounded latency to the commit pipeline.
        # Iter 212m-132 — when diff-mode is active, append a per-call
        # nudge re-emphasising the changed-lines constraint to
        # counter the LLM's tendency to drift back to whole-file
        # review when files are large.
        sys_prompt = _VERIFY_SYSTEM
        if diff_mode_active:
            sys_prompt += (
                "\n\nDIFF-MODE ADDENDUM: This review is for an "
                "INCREMENTAL PATCH. Each file's `CHANGED_LINES` "
                "header lists the line numbers that were added or "
                "modified. ONLY emit findings whose `line` is in "
                "the CHANGED_LINES set for that file. Pre-existing "
                "code is OUT OF SCOPE for this review — that's a "
                "separate audit. Include the line number in every "
                "finding so the caller can verify."
            )

        # Iter 212m-172 — Primary → rescue model fallback.
        # If Claude (primary) fails (timeout / 5xx / empty), try DeepSeek
        # (rescue) so the second-agent review still runs in production
        # instead of silently dropping to the regex-only floor.
        model_used = _VERIFY_MODEL
        raw = ""
        primary_error: Optional[str] = None
        # ORA Brain Part 4, F5 — record-only; no routing change.
        from services.brain_gateway import record_wrap
        from services.loop_token_ledger import (
            current_ambient_user_id,
            current_surface_context,
        )
        _bg_user_id = current_ambient_user_id()
        _bg_ctx = current_surface_context()
        try:
            raw = await asyncio.wait_for(
                record_wrap(
                    call_openrouter_model(
                        model=_VERIFY_MODEL,
                        system=sys_prompt,
                        user="Review the following patch:\n\n" + envelope,
                        max_tokens=2000,
                        temperature=0.0,
                        feature="Vanguard Verify",
                    ),
                    user_id=_bg_user_id, project_id=_bg_ctx.get("project_id"),
                    task_id=_bg_ctx.get("task_id"), task_type="vanguard_verify",
                    model=_VERIFY_MODEL, surface="vanguard_verify",
                ),
                timeout=30.0,
            )
            if not (raw or "").strip():
                raise ValueError("empty response from primary")
        except Exception as _pe:
            primary_error = f"{type(_pe).__name__}: {_pe!r}"[:200]
            logger.warning(
                "vanguard-verify primary failed (%s) — trying rescue model %s",
                primary_error, _VERIFY_RESCUE_MODEL,
            )
            try:
                raw = await asyncio.wait_for(
                    record_wrap(
                        call_openrouter_model(
                            model=_VERIFY_RESCUE_MODEL,
                            system=sys_prompt,
                            user="Review the following patch:\n\n" + envelope,
                            max_tokens=2000,
                            temperature=0.0,
                            feature="Vanguard Verify",
                        ),
                        user_id=_bg_user_id, project_id=_bg_ctx.get("project_id"),
                        task_id=_bg_ctx.get("task_id"), task_type="vanguard_verify",
                        model=_VERIFY_RESCUE_MODEL, surface="vanguard_verify",
                    ),
                    timeout=30.0,
                )
                model_used = _VERIFY_RESCUE_MODEL
                if not (raw or "").strip():
                    raise ValueError("empty response from rescue")
            except Exception as _re:
                # Both failed — fall through to the regex floor.
                logger.warning(
                    "vanguard-verify rescue ALSO failed (%r) — leaving regex floor",
                    _re,
                )
                return {"pass": True, "findings": [],
                        "summary": (
                            f"verify-agent skipped (primary={primary_error}, "
                            f"rescue={type(_re).__name__})"
                        )[:500],
                        "model":   ""}

        text = (raw or "").strip()
        # Strip ```json fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rstrip("`").rstrip()
            if text.endswith("```"):
                text = text[: -3].rstrip()
        if text.startswith("json"):
            text = text[4:].lstrip()

        # 2026-11-25 audit Part IX (b) — FAIL-CLOSED parsing. The old
        # code did a GLOBAL str.replace("True"→"true" …) over the whole
        # response (corrupting string values: "NoneType"→"nullType")
        # and then `bool(data.get("pass", True))` — so unparseable
        # output PASSED, and a string "false" PASSED (bool("false")
        # is True). New rules:
        #   • strict json.loads first;
        #   • fallback: replace Python literals ONLY outside string
        #     literals (a small scanner, not a global replace), then
        #     re-parse; then a {…} extraction attempt, same way;
        #   • anything still unparseable, or a missing/non-boolean
        #     "pass", FAILS CLOSED — with a CRITICAL finding so the
        #     severity-based blocking rule in verify_patch() also
        #     blocks (a bare pass=False with no findings would not).
        #   • "pass" is True ONLY for real JSON true (or the string
        #     "true"); the string "false" is False.
        data = _parse_review_json(text)
        if data is None:
            return {
                "pass":     False,
                "findings": [{
                    "file": "", "line": 0, "severity": "CRITICAL",
                    "rule": "verify-agent-unparseable",
                    "message": ("verify-agent returned unparseable "
                                "output — failing closed; no usable "
                                "verdict was produced"),
                }],
                "summary":  "verify-agent unparseable output (fail-closed)",
                "model":    model_used,
            }
        raw_pass = data.get("pass")
        coerced_pass = raw_pass is True or (
            isinstance(raw_pass, str) and raw_pass.strip().lower() == "true"
        )
        raw_findings = data.get("findings") or []
        findings = raw_findings if isinstance(raw_findings, list) else []
        # A missing or non-boolean "pass" is not a verdict at all —
        # fail closed with a CRITICAL finding so the severity rule in
        # verify_patch() blocks (a bare pass=False with no findings
        # would not). An EXPLICIT false (bool, or the string "false")
        # is a usable verdict: the severity rule decides.
        verdict_usable = raw_pass is True or raw_pass is False or (
            isinstance(raw_pass, str)
            and raw_pass.strip().lower() in ("true", "false")
        )
        if not verdict_usable:
            coerced_pass = False
            findings = [{
                "file": "", "line": 0, "severity": "CRITICAL",
                "rule": "verify-agent-no-verdict",
                "message": ("verify-agent returned no usable pass verdict "
                            "(missing or non-boolean 'pass') — failing closed"),
            }] + findings
        return {
            "pass":     coerced_pass,
            "findings": findings,
            "summary":  str(data.get("summary") or "")[:500],
            "model":    model_used,
        }
    except Exception as e:
        logger.warning("vanguard-verify LLM call failed (%r) — leaving regex floor",
                       e)
        return {"pass": True, "findings": [],
                "summary": f"verify-agent skipped (error: {type(e).__name__})",
                "model":   ""}


async def _e2b_smoke(file_blocks: dict) -> dict:
    """If the patch contains Python code with functions/classes, smoke-
    test the imports inside E2B so we catch runtime ImportError and
    NameError that static AST misses. Returns {pass, output, skipped}."""
    py_files = {p: c for p, c in (file_blocks or {}).items()
                if p.endswith(".py") and c}
    if not py_files or not _has_executable_python(py_files):
        return {"pass": True, "skipped": True,
                "reason": "no executable python in patch"}
    # Build a multi-file import probe: write each file then `import`
    # it by module path so we catch SyntaxError, ImportError, top-level
    # NameError, etc. (2026-11-25 audit Part IX (c): the old probe
    # only ran compile(), which checks syntax and never executes
    # top-level code — bad imports and undefined names sailed
    # through. The sys.path setup below is what makes the import
    # resolve to the just-written file instead of an installed
    # same-named module. Top-level side effects DO run — that is the
    # point of the E2B sandbox boundary.)
    probe_lines: list[str] = ["import os, sys, importlib"]
    for i, (path, content) in enumerate(py_files.items()):
        safe = path.replace("/", "_").replace(".py", "")
        probe_lines.append(f"os.makedirs(os.path.dirname({path!r}) or '.', exist_ok=True)")
        probe_lines.append(f"open({path!r}, 'w').write({content!r})")
        probe_lines.append(f"sys.path.insert(0, os.path.dirname({path!r}) or '.')")
    # Actually import each module (not compile()): catches SyntaxError
    # AND ImportError AND top-level NameError.
    for path in py_files:
        mod = path[:-3].replace("/", ".") if path.endswith(".py") else path
        probe_lines.append(f"importlib.import_module({mod!r})")
    probe = "\n".join(probe_lines)
    try:
        from .sandbox_runner import run_python_check
        res = await run_python_check(probe, filename="vanguard_smoke.py", timeout=20)
        if res.get("skipped"):
            return {"pass": True, "skipped": True,
                    "reason": res.get("reason", "sandbox unavailable")}
        if not res.get("ok"):
            return {"pass": False, "skipped": False,
                    "stderr": (res.get("stderr") or "")[:1200],
                    "stdout": (res.get("stdout") or "")[:600]}
        return {"pass": True, "skipped": False, "stdout": (res.get("stdout") or "")[:300]}
    except Exception as e:
        logger.warning("vanguard E2B smoke failed: %r", e)
        return {"pass": True, "skipped": True, "reason": str(e)}


async def verify_patch(
    file_blocks: dict,
    repo_ctx: str = "unknown",
    *,
    mode: str = "swift",
    base_blocks: Optional[dict] = None,
) -> dict:
    """Top-level entrypoint. Combines:
      1) regex Vanguard scan (the 24 baseline patterns)
      2) separate Vanguard Verify Agent (LLM second opinion)
      3) E2B smoke import if patch has executable Python

    Blocking rule (Iter 212m-41 / 212m-42):
      • Regex CRITICAL ALWAYS blocks (real secrets / dangerous APIs).
      • LLM findings block ONLY when their severity meets the
        configured threshold for the active `mode` (Swift / Pro / Maxx).
      • E2B smoke-import failure ALWAYS blocks (syntax/import errors).
      • 2026-11-25 audit Part IX (d): `agent.pass` in the returned
        payload is the BLOCKING decision (not the reviewer's raw
        self-assessment — that lives in `agent.self_assessed_pass`),
        so the two can never diverge silently. A crashed LLM/E2B
        task fails closed instead of discarding the regex verdict.
      • Admin can flip the master enabled flag from `/admin/vanguard`
        to skip the LLM + E2B passes entirely — regex floor still gates
        on its own CRITICAL findings.

    Iter 212m-132 — DIFF-AWARE mode.
      When the caller supplies `base_blocks={path: pre_edit_content}`,
      we compute the per-file changed-line set and:
        • drop any regex finding whose `line` is in PRE-existing code
        • prompt the LLM with `CHANGED_LINES:` headers + an explicit
          "only flag introduced issues" addendum
        • drop any LLM finding outside the changed-line set as
          double-insurance (the LLM sometimes slips)
      The dropped findings are still surfaced in
      `regex_skipped_preexisting` / `llm_skipped_preexisting` for
      audit, but they do NOT block the commit.
      When `base_blocks` is None or empty → behaviour is identical
      to the legacy full-file scan (backward-compatible).
    """
    from .vanguard_config import get_mode_settings
    enabled, block_level = await get_mode_settings(mode)

    # Iter 212m-132 — compute changed-line map ONCE; reuse for regex
    # filtering AND for the LLM envelope.
    line_map: Optional[dict[str, set[int]]] = None
    if base_blocks:
        line_map = changed_lines_map(base_blocks, file_blocks or {})

    findings: list[dict] = []
    regex_findings_all = scan_file_blocks(file_blocks or {})
    if line_map is not None:
        regex_findings, regex_skipped = filter_findings_to_changed_lines(
            regex_findings_all, line_map,
        )
    else:
        regex_findings = regex_findings_all
        regex_skipped  = []
    findings.extend(regex_findings)
    regex_blocked = has_critical(regex_findings)

    if not enabled:
        return {
            "pass":     not regex_blocked,
            "findings": findings,
            "summary":  (
                "regex: "
                f"{'BLOCK' if regex_blocked else 'pass'} "
                f"({len(regex_findings)} findings"
                + (f", {len(regex_skipped)} pre-existing skipped" if regex_skipped else "")
                + ") | verify-agent + e2b disabled by admin"
            ),
            "regex":    {"blocked": regex_blocked,
                         "count":   len(regex_findings),
                         "skipped_preexisting": regex_skipped},
            "agent":    {"pass": True, "findings": [], "summary": "disabled"},
            "e2b":      {"pass": True, "skipped": True, "reason": "disabled"},
            "mode":     mode,
            "block_level": "OFF",
            "diff_mode": line_map is not None,
        }

    def _blocks(severity: str) -> bool:
        sev = (severity or "").upper()
        if block_level == "OFF":
            return False
        if block_level == "HIGH":
            return sev in ("CRITICAL", "HIGH")
        return sev == "CRITICAL"

    # Always run the second agent + E2B in parallel — they don't depend
    # on each other and the latency budget matters.
    # 2026-11-25 audit Part IX (d): return_exceptions=True so ONE
    # failing task cannot crash verify_patch and discard the
    # already-computed regex verdict (the caller falls through to
    # commit on the crash today). An exception result fails CLOSED.
    llm_task = asyncio.create_task(_llm_review(file_blocks or {}, repo_ctx,
                                               line_map=line_map))
    e2b_task = asyncio.create_task(_e2b_smoke(file_blocks or {}))
    llm_review, e2b_result = await asyncio.gather(llm_task, e2b_task,
                                                  return_exceptions=True)
    if isinstance(llm_review, BaseException):
        logger.warning("vanguard-verify LLM task crashed (%r) — failing closed",
                       llm_review)
        llm_review = {
            "pass": False,
            "findings": [{
                "file": "", "line": 0, "severity": "CRITICAL",
                "rule": "verify-agent-crashed",
                "message": (f"verify-agent task raised "
                            f"{type(llm_review).__name__} — failing closed"),
            }],
            "summary": f"verify-agent crashed ({type(llm_review).__name__})",
            "model": "",
        }
    if isinstance(e2b_result, BaseException):
        logger.warning("vanguard-verify E2B task crashed (%r) — failing closed",
                       e2b_result)
        e2b_result = {
            "pass": False, "skipped": False,
            "stderr": f"e2b smoke task raised {type(e2b_result).__name__}",
        }

    # Iter 212m-132 — drop LLM findings that landed on pre-existing
    # lines as a double-check.  The LLM was told via the prompt
    # addendum to respect CHANGED_LINES, but Claude sometimes drifts
    # so we enforce it server-side too.
    llm_skipped: list[dict] = []
    if line_map is not None:
        kept, llm_skipped = filter_findings_to_changed_lines(
            llm_review.get("findings", []) or [], line_map,
        )
        llm_review["findings"] = kept

    blocking_llm_findings = [
        f for f in llm_review.get("findings", []) or []
        if _blocks(f.get("severity", ""))
    ]
    llm_blocked = bool(blocking_llm_findings) and block_level != "OFF"

    # 2026-11-25 audit Part IX (d) — reconcile the two pass values.
    # The reviewer's own `pass` was never read here, so `agent.pass`
    # and the top-level `pass` could disagree silently in both
    # directions. The documented rule stands (blocking is severity-
    # based: pass=False + only LOW findings does NOT block), but the
    # REPORTED agent.pass now reflects the blocking decision, and the
    # reviewer's raw self-assessment is preserved separately as
    # `self_assessed_pass` — a divergence is explicit, never silent.
    # (A fail-closed review — unparseable output, crashed task — now
    # carries a CRITICAL finding, so it blocks through this same rule
    # instead of being overwritten back to pass.)
    llm_self_pass = llm_review.get("pass") is True
    llm_review["self_assessed_pass"] = llm_self_pass
    llm_review["pass"] = not llm_blocked
    llm_diverged = llm_self_pass == llm_blocked  # self-assessment vs verdict disagree

    for f in llm_review.get("findings", []) or []:
        f.setdefault("source", "vanguard_verify_agent")
        findings.append(f)
    # Fail-closed on a missing/non-bool e2b pass (was .get("pass", True)).
    e2b_blocked = e2b_result.get("pass") is not True

    overall_pass = not (regex_blocked or llm_blocked or e2b_blocked)

    summary_parts = []
    skip_note = (f", {len(regex_skipped)} pre-existing skipped"
                 if regex_skipped else "")
    summary_parts.append(f"regex: {'BLOCK' if regex_blocked else 'pass'} "
                         f"({len(regex_findings)} findings{skip_note})")
    if llm_review.get("model"):
        n_findings = len(llm_review.get("findings", []) or [])
        n_block    = len(blocking_llm_findings)
        llm_skip_note = (f", {len(llm_skipped)} pre-existing skipped"
                         if llm_skipped else "")
        diverge_note = ""
        if llm_diverged:
            diverge_note = (
                " [reviewer self-assessed "
                f"pass={llm_self_pass}, verdict pass={not llm_blocked} "
                "per severity rule]"
            )
        summary_parts.append(
            f"verify-agent ({llm_review['model']}, {mode}/{block_level}): "
            f"{'BLOCK' if llm_blocked else 'pass'} "
            f"({n_findings} findings, {n_block} ≥{block_level}{llm_skip_note})"
            f"{diverge_note}"
        )
    else:
        summary_parts.append("verify-agent: skipped")
    if e2b_result.get("skipped"):
        summary_parts.append(f"e2b: skipped ({e2b_result.get('reason', '')})")
    else:
        summary_parts.append(f"e2b: {'BLOCK' if e2b_blocked else 'pass'}")

    return {
        "pass":     overall_pass,
        "findings": findings,
        "summary":  " | ".join(summary_parts),
        "regex":    {"blocked": regex_blocked,
                     "count":   len(regex_findings),
                     "skipped_preexisting": regex_skipped},
        "agent":    {**llm_review, "skipped_preexisting": llm_skipped},
        "e2b":      e2b_result,
        "mode":     mode,
        "block_level": block_level,
        "diff_mode": line_map is not None,
    }
