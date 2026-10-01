"""
services/commit_guards.py — S7 commit safety guards for every commit path.

Wired at the single writer choke point (`github_api_writer.commit_files`)
so chat edits, the MCP writer, security fixes and the task workers all
inherit it.  Follows the `write_guard` convention: per-rule mode in
`guard_config`, default "warn" (shadow: log + event, never blocks).  An
owner flips the rule to "block" after reviewing the shadow events (S13:
that flip is never done by code).

Rules, per file, comparing the old (current HEAD) body with the new one:
  • deletes more than 30% of the old lines            → mass_delete
  • new size below 50% of old size                    → shrunk
  • new content fails to parse (py / json / js-ts)    → syntax
  • a top-level function/class of the old file is gone → symbol_removed
Vendored/generated dirs (node_modules, vendor, .agent, dist, build,
benchmarks, .next, coverage) are skipped.  New files and empty→content
changes are never flagged.  Never raises in warn mode; any internal
failure fails OPEN (the guard must not be the thing that breaks a commit).
"""
from __future__ import annotations

import ast
import asyncio
import difflib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

RULE_COMMIT_SAFETY = "commit_safety"
MAX_DELETE_RATIO = 0.30
MIN_SIZE_RATIO = 0.50
MIN_LINES_FOR_RATIOS = 10          # tiny files legitimately change a lot
MAX_FILES_CHECKED = 25
FETCH_TIMEOUT_S = 6.0
TOTAL_BUDGET_S = 10.0

_SKIP_DIRS = ("node_modules/", "vendor/", ".agent/", "dist/", "build/",
              "benchmarks/", ".next/", "coverage/")
_JS_SYMBOL = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function\*?|class)\s+([A-Za-z_$][\w$]*)", re.M)
_JS_CONST_FN = re.compile(
    r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", re.M)


class CommitGuardBlockedError(PermissionError):
    def __init__(self, violations: list[dict]):
        self.violations = violations
        first = violations[0] if violations else {}
        super().__init__(
            "This change was stopped by a safety check: "
            f"{first.get('plain', 'it looks like it would remove a lot of existing code')}. "
            "Nothing was committed."
        )


def is_vendored(path: str) -> bool:
    p = "/" + path.lstrip("/")
    return any(("/" + d) in p for d in _SKIP_DIRS)


def _py_symbols(src: str) -> Optional[set[str]]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _js_symbols(src: str) -> set[str]:
    return set(_JS_SYMBOL.findall(src)) | set(_JS_CONST_FN.findall(src))


def _syntax_error(path: str, content: str) -> Optional[str]:
    if not content.strip():
        return None
    if path.endswith(".py"):
        try:
            ast.parse(content)
        except SyntaxError as e:
            return f"line {e.lineno or 1}: {e.msg}"
    elif path.endswith(".json"):
        try:
            json.loads(content)
        except ValueError as e:
            return f"invalid JSON: {e}"
    elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
        from services.cto_pipeline_steps import _check_js_syntax
        return _check_js_syntax(path, content)
    return None


def evaluate_file(path: str, old: Optional[str], new: str) -> list[dict]:
    """Pure.  Returns violations (possibly empty).  `old=None` = new file."""
    if is_vendored(path):
        return []
    out: list[dict] = []
    err = _syntax_error(path, new)
    if err:
        out.append({"rule": "syntax", "path": path, "detail": err,
                    "plain": f"the new version of {path.rsplit('/', 1)[-1]} has a typing mistake that would break it"})
    if not old or not old.strip():
        return out
    old_lines, new_lines = old.splitlines(), new.splitlines()
    if len(old_lines) >= MIN_LINES_FOR_RATIOS:
        sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
        removed = sum(i2 - i1 for tag, i1, i2, _, _ in sm.get_opcodes() if tag in ("delete", "replace"))
        if removed / len(old_lines) > MAX_DELETE_RATIO:
            out.append({"rule": "mass_delete", "path": path,
                        "detail": f"{removed}/{len(old_lines)} lines removed",
                        "plain": f"it would remove {round(100 * removed / len(old_lines))}% of {path.rsplit('/', 1)[-1]}"})
        if len(new) < MIN_SIZE_RATIO * len(old):
            out.append({"rule": "shrunk", "path": path,
                        "detail": f"{len(new)} bytes vs {len(old)}",
                        "plain": f"{path.rsplit('/', 1)[-1]} would end up less than half its size"})
    if path.endswith(".py"):
        a, b = _py_symbols(old), _py_symbols(new)
        gone = sorted((a or set()) - (b or set())) if b is not None and a is not None else []
    elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
        gone = sorted(_js_symbols(old) - _js_symbols(new))
    else:
        gone = []
    if gone:
        out.append({"rule": "symbol_removed", "path": path, "detail": "removed: " + ", ".join(gone[:8]),
                    "plain": f"it would delete existing code that wasn't part of the request ({gone[0]})"})
    return out


def evaluate_files(files: dict[str, str], olds: dict[str, Optional[str]]) -> list[dict]:
    v: list[dict] = []
    for path, new in files.items():
        v.extend(evaluate_file(path, olds.get(path), new))
    return v


async def _fetch_old(client, headers: dict, base: str, ref: str, path: str) -> Optional[str]:
    try:
        r = await asyncio.wait_for(client.get(
            f"{base}/contents/{path}", params={"ref": ref},
            headers={**headers, "Accept": "application/vnd.github.raw+json"}), FETCH_TIMEOUT_S)
        return r.text if r.status_code == 200 else None
    except Exception:                                       # noqa: BLE001
        return None


async def check_commit(db, *, files: dict[str, str], owner: str, repo: str, branch: str,
                       head_sha: str, token: str, headers: dict, client, api: str) -> list[dict]:
    """Evaluate a pending commit.  Warn mode (default): log + record, return
    violations, never raise.  Block mode: raise CommitGuardBlockedError.
    Any internal failure fails open."""
    try:
        from services.write_guard import get_mode
        mode = await get_mode(db, RULE_COMMIT_SAFETY)
        targets = {p: c for p, c in files.items() if not is_vendored(p)}
        if not targets:
            return []
        base = f"{api}/repos/{owner}/{repo}"
        paths = list(targets)[:MAX_FILES_CHECKED]

        async def run():
            olds_list = await asyncio.gather(*[_fetch_old(client, headers, base, head_sha, p) for p in paths])
            olds = dict(zip(paths, olds_list))
            return await asyncio.to_thread(evaluate_files, {p: targets[p] for p in paths}, olds)
        violations = await asyncio.wait_for(run(), TOTAL_BUDGET_S)
    except Exception as e:                                  # noqa: BLE001
        logger.warning("[commit_guards] failed open: %r", e)
        return []
    if not violations:
        return []
    event = "CG_BLOCK" if mode == "block" else "CG_WARN"
    logger.warning("[commit_guards] %s %s/%s@%s: %s", event, owner, repo, branch,
                   [(v["rule"], v["path"]) for v in violations])
    if db is not None:
        try:
            await db.guardrail_events.insert_one({
                "event": event, "rule": RULE_COMMIT_SAFETY, "mode": mode,
                "violations": [{k: v[k] for k in ("rule", "path", "detail")} for v in violations],
                "owner": owner, "repo": repo, "branch": branch, "ts": datetime.now(timezone.utc),
            })
        except Exception:                                   # noqa: BLE001
            pass
    if mode == "block":
        raise CommitGuardBlockedError(violations)
    return violations
