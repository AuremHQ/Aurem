from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import time
from typing import Optional

from cto_services.db import get_db
from .repo_context import _fetch_file as _gh_fetch_file
from .repo_context import GithubAuthError

def _is_binary_data(content: str) -> bool:
    """Check if content contains non-UTF-8 byte sequences.
    Returns True if binary data is detected, False for clean UTF-8 text.
    """
    try:
        content.encode('utf-8').decode('utf-8')
        return False
    except UnicodeError:
        return True

def _github_access_revoked_error(owner: str, repo: str, status_code: int) -> dict:
    return {
        "ok": False,
        "error": (
            f"❌ GITHUB ACCESS REVOKED (HTTP {status_code}) for "
            f"{owner}/{repo}. This is NOT a missing file and NOT an "
            f"AUREM/auremcto.com API problem - GitHub itself rejected "
            f"the request. The user (or someone on their GitHub org) "
            f"has revoked the GitHub App installation, removed this "
            f"repo from it, deleted the Personal Access Token, or the "
            f"OAuth/PAT token expired. STOP calling repo tools - they "
            f"will all fail the same way until access is restored. "
            f"Tell the user clearly: 'GitHub access to {owner}/{repo} "
            f"was revoked - reconnect this repo from the project "
            f"settings (reinstall/grant the GitHub App, or add a new "
            f"token) to keep working.' Do not guess at file paths or "
            f"blame an unrelated API."
        ),
    }

_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_\.\-/ ]")

def _is_safe_repo_path(path: str) -> bool:
    if not path or not isinstance(path, str):
        return False
    return not _UNSAFE_PATH_CHARS.search(path)

def _run_subprocess_pgkill(cmd: list[str], timeout: float):
    import os
    import signal
    import subprocess

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            proc.communicate()
        raise

def _run_syntax_check(*, content: str, file_path: str, ext: str) -> dict:
    import os
    import subprocess
    import tempfile

    if not content:
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": "empty_content"}
    try:
        tmp = tempfile.NamedTemporaryFile(
            suffix=ext, mode="w", delete=False, encoding="utf-8",
        )
        tmp.write(content)
        tmp.close()
        tmp_path = tmp.name
    except Exception as e:
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": f"tmp_write:{type(e).__name__}"}

    try:
        if ext == ".py":
            try:
                result = _run_subprocess_pgkill(
                    ["python", "-m", "py_compile", tmp_path], timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                return {"has_errors": False, "errors": "",
                        "skipped": True,
                        "reason": f"py_check:{type(e).__name__}"}
            if result.returncode != 0:
                return {"has_errors": True,
                        "errors": (result.stderr or result.stdout or "")[:500],
                        "skipped": False}
        elif ext in (".js", ".jsx"):
            try:
                result = _run_subprocess_pgkill(
                    ["node", "--check", tmp_path], timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                return {"has_errors": False, "errors": "",
                        "skipped": True,
                        "reason": f"js_check:{type(e).__name__}"}
            if result.returncode != 0:
                return {"has_errors": True,
                        "errors": (result.stderr or "")[:500],
                        "skipped": False}
        elif ext in (".ts", ".tsx"):
            try:
                result = _run_subprocess_pgkill(
                    ["npx", "tsc", "--noEmit", "--allowJs",
                     "--jsx", "preserve", "--target", "ES2020",
                     "--module", "esnext", "--moduleResolution", "node",
                     tmp_path], timeout=15,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                return {"has_errors": False, "errors": "",
                        "skipped": True,
                        "reason": f"ts_check:{type(e).__name__}"}
            if result.returncode != 0:
                err = (result.stdout or result.stderr or "")[:500]
                if "error TS1" in err:
                    return {"has_errors": True, "errors": err,
                            "skipped": False}
                return {"has_errors": False, "errors": "",
                        "skipped": True,
                        "reason": "ts_only_type_errors"}
        return {"has_errors": False, "errors": "", "skipped": False}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception as _e:
            logger.debug("[silent-catch] services/local_tools.py:122 in _run_syntax_check - %r", _e)
            pass

from .web_skills import (
    WEB_TOOLS as _WEB_TOOLS,
    WEB_TOOL_SPECS as _WEB_TOOL_SPECS,
)
from .browser_tools import browser as _browser_tool
from .dev_skills import (
    DEV_TOOLS as _DEV_TOOLS,
    DEV_TOOL_SPECS as _DEV_TOOL_SPECS,
)
from .vercel_skills import (
    VERCEL_TOOLS as _VERCEL_TOOLS,
    VERCEL_TOOL_SPECS as _VERCEL_TOOL_SPECS,
)
from .context7_skills import (
    CONTEXT7_TOOLS as _CONTEXT7_TOOLS,
    CONTEXT7_TOOL_SPECS as _CONTEXT7_TOOL_SPECS,
)
from .sequential_thinking_skills import (
    SEQUENTIAL_THINKING_TOOLS as _SEQUENTIAL_THINKING_TOOLS,
    SEQUENTIAL_THINKING_TOOL_SPECS as _SEQUENTIAL_THINKING_TOOL_SPECS,
)

logger = logging.getLogger(__name__)

MAX_FILE_CHARS = 15_000
MAX_FILES_BULK = 6

async def _resolve_project(user_id: str, project_id: str) -> dict | None:
    if not user_id:
        return None
    db = get_db()
    if db is None:
        return None

    pid_clean = (project_id or "").strip()
    if not pid_clean or pid_clean == "home":
        return None

    proj = await db.cto_projects.find_one(
        {"project_id": pid_clean, "user_id": user_id}
    )
    if proj is None:
        return None

    from services.pat_vault import get_repo_token_or_error
    token, err_code, err_detail = await get_repo_token_or_error(proj)
    if err_code:
        logger.warning("local_tools._resolve_project: App auth failed "
                       "(%s): %s", err_code, err_detail)
    proj["github_token"] = token
    return proj

def _repo_ctx_from(ctx: dict) -> Optional[dict]:
    bc = (ctx or {}).get("bin_ctx")
    if bc is None:
        return None
    caller_uid = (ctx or {}).get("user_id") or ""
    if getattr(bc, "bin_id", None) and caller_uid and bc.bin_id != caller_uid:
        logger.warning(
            "_repo_ctx_from: bin_ctx.bin_id=%s != ctx.user_id=%s - refusing",
            bc.bin_id, caller_uid,
        )
        return None
    if not (getattr(bc, "repo_owner", "") and getattr(bc, "repo_name", "")):
        return None
    return {
        "ok":         True,
        "owner":      bc.repo_owner,
        "repo":       bc.repo_name,
        "branch":     bc.branch or "main",
        "token":      bc.pat,
        "is_founder": bool(getattr(bc, "is_founder", False)),
        "bin_id":     bc.bin_id,
        "pid":        bc.pid,
    }

_NO_BIN_CTX_ERROR = {
    "ok": False,
    "error": (
        "No project selected. Please select a project from the sidebar "
        "and try again - repo tools cannot run against Home."
    ),
    "error_class": "no_bin_ctx",
}

def _verify_ctx(ctx: dict):
    bc = (ctx or {}).get("bin_ctx")
    if bc is None:
        return None
    caller_uid = (ctx or {}).get("user_id") or ""
    if getattr(bc, "bin_id", None) and caller_uid and bc.bin_id != caller_uid:
        logger.warning(
            "_verify_ctx: bin_ctx.bin_id=%s != ctx.user_id=%s - refusing",
            bc.bin_id, caller_uid,
        )
        return None
    boundary_off = getattr(bc, "ora_boundary_active", True) is False
    if boundary_off and not bool(getattr(bc, "is_founder", False)):
        logger.warning(
            "_verify_ctx: ora_boundary_active=False but is_founder=False "
            "- refusing (boundary tamper)",
        )
        return None
    return bc

def _slice_content(content: str, lines: list | None, max_chars: int) -> tuple[str, bool]:
    if isinstance(lines, list) and len(lines) == 2:
        try:
            start = max(int(lines[0]), 1)
            end   = max(int(lines[1]), start)
            content = "\n".join(content.splitlines()[start - 1:end])
        except Exception as _e:
            logger.debug("[silent-catch] services/local_tools.py:301 in _slice_content - %r", _e)
            pass
    total = len(content)
    truncated = total > max_chars
    if truncated:
        content = (
            content[:max_chars]
            + f"\n... [truncated - {total} total chars, showing first "
            f"{max_chars}. Use lines=[start,end] arg to fetch specific "
            "sections]"
        )
    return content, truncated

_CHUNK_LIMIT = 12_000
_STRUCTURE_RX = re.compile(
    r"\s*(def |async def |class |@router\."
    r"|export (default |function |const )|function )"
)

def _apply_chunking(content: str, args: dict | None) -> dict:
    if content is None:
        content = ""
    if len(content) <= _CHUNK_LIMIT:
        return {"ok": True, "content": content, "truncated": False}

    src_lines = content.splitlines()
    total = len(src_lines)

    line_range = (args or {}).get("lines")
    if isinstance(line_range, list) and len(line_range) == 2:
        try:
            s, e = int(line_range[0]), int(line_range[1])
        except (TypeError, ValueError):
            s, e = 0, 0
        chunk = "\n".join(src_lines[s:e])
        return {
            "ok": True,
            "content": chunk,
            "truncated": True,
            "total_lines": total,
            "note": (
                f"Lines {s}-{e} of {total}. "
                "Use lines=[start,end] for other sections."
            ),
        }

    preview = "\n".join(src_lines[:200])
    structure = [
        f"L{i+1}: {ln.strip()}"
        for i, ln in enumerate(src_lines)
        if _STRUCTURE_RX.match(ln)
    ][:40]

    return {
        "ok": True,
        "content": preview,
        "truncated": True,
        "total_lines": total,
        "structure": structure,
        "next_call_required": True,
        "next_call_hint": {
            "tool": "read_repo_file",
            "args_template": {"path": "<same path>", "lines": ["<start>", "<end>"]},
            "reason": "preview-only - answer would be incomplete without a targeted slice",
        },
        "note": (
            f"File has {total} lines. Showing 1-200. "
            "You MUST call this tool again with lines=[start,end] "
            "before answering - do not respond from the preview alone."
        ),
    }

_REPO_STRUCTURE_CACHE: dict[str, dict[str, list[dict]]] = {}
_REPO_CACHE_MAX_PROJECTS = 100
_REPO_CACHE_MAX_FILES_PER_PROJECT = 200
_REPO_CACHE_MAX_SYMBOLS_PER_FILE = 100

def _extract_symbols(content: str) -> list[dict]:
    if not content:
        return []
    out: list[dict] = []
    for i, ln in enumerate(content.splitlines()):
        if _STRUCTURE_RX.match(ln):
            out.append({"line": i + 1, "symbol": ln.strip()})
            if len(out) >= _REPO_CACHE_MAX_SYMBOLS_PER_FILE:
                break
    return out

def _cache_set(project_id: str, path: str, symbols: list[dict]) -> None:
    if not project_id or not path:
        return
    if (project_id not in _REPO_STRUCTURE_CACHE
            and len(_REPO_STRUCTURE_CACHE) >= _REPO_CACHE_MAX_PROJECTS):
        first_key = next(iter(_REPO_STRUCTURE_CACHE))
        _REPO_STRUCTURE_CACHE.pop(first_key, None)
    bucket = _REPO_STRUCTURE_CACHE.setdefault(project_id, {})
    if (path not in bucket
            and len(bucket) >= _REPO_CACHE_MAX_FILES_PER_PROJECT):
        first_path = next(iter(bucket))
        bucket.pop(first_path, None)
    bucket[path] = symbols

async def _update_structure_cache(
    project_id: str | None, path: str, content: str,
) -> None:
    try:
        if not project_id or project_id == "home" or not path:
            return
        symbols = _extract_symbols(content or "")
        if not symbols:
            return
        _cache_set(project_id, path, symbols)
    except Exception as e:
        logger.debug("structure cache update skipped: %r", e)

def _cache_get(project_id: str, path: str | None = None) -> dict | list | None:
    bucket = _REPO_STRUCTURE_CACHE.get(project_id)
    if bucket is None:
        return None
    if path is None:
        return bucket
    return bucket.get(path)

def _cache_invalidate(project_id: str, path: str | None = None) -> None:
    if not project_id:
        return
    if path is None:
        _REPO_STRUCTURE_CACHE.pop(project_id, None)
        return
    bucket = _REPO_STRUCTURE_CACHE.get(project_id)
    if bucket is not None:
        bucket.pop(path, None)

async def read_repo_file(ctx: dict, args: dict) -> dict:
    path = (args or {}).get("path")

    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing required arg `path`"}
    if path.startswith("/") or ".." in path.split("/"):
        return {"ok": False, "error": "Invalid path - no absolute paths or traversal"}

    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR

    owner, repo, branch, token = rc["owner"], rc["repo"], rc["branch"], rc["token"]
    project_id = rc["pid"]

    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    try:
        content = await _gh_fetch_file(owner, repo, path, branch, token)
    except GithubAuthError as e:
        return _github_access_revoked_error(owner, repo, e.status_code)
    if content is None:
        return {
            "ok":    False,
            "path":  path,
            "status": 404,
            "error": (
                f"❌ FILE NOT FOUND: `{path}` does not exist on "
                f"{owner}/{repo}@{branch}. STOP guessing paths. "
                "Your next tool call MUST be `list_repo_files` with a glob "
                "(e.g. `**/auth*.py`, `**/*router*.py`) to DISCOVER the "
                "real paths in this repo. Do not write a plan, do not "
                "produce a handoff brief, do not cite any file paths - "
                "until you have called list_repo_files and seen the "
                "actual layout. Most repos do not follow the layout you "
                "expect from training data."
            ),
        }

    if _is_binary_data(content):
        return {
            "ok": False,
            "error": (
                f"❌ BINARY DATA DETECTED in {path} - this file contains "
                "non-UTF-8 byte sequences. Direct chat input must be "
                "textual content only. For binary files, use proper file "
                "uploads instead of direct input."
            ),
            "status": 415,
        }

    chunked = _apply_chunking(content, args or {})

    try:
        asyncio.create_task(
            _update_structure_cache(project_id, path, content),
        )
    except Exception as _e:
        logger.debug("[silent-catch] services/local_tools.py:556 in read_repo_file - %r", _e)
        pass

    return {
        **chunked,
        "path":   path,
        "branch": branch,
    }

async def read_repo_files(ctx: dict, args: dict) -> dict:
    user_id = ctx.get("user_id")
    project_id = ctx.get("project_id")
    paths = (args or {}).get("paths") or []
    line_range = (args or {}).get("lines")

    if not isinstance(paths, list) or not paths:
        return {"ok": False, "error": "Missing required arg `paths` (list of strings)"}

    _raw_paths = [p for p in paths if isinstance(p, str) and p]
    paths = list(dict.fromkeys(_raw_paths))[:MAX_FILES_BULK]
    _dropped = list(dict.fromkeys(_raw_paths))[MAX_FILES_BULK:]

    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, branch, token = rc["owner"], rc["repo"], rc["branch"], rc["token"]
    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    async def _fetch_one(path: str) -> dict:
        if path.startswith("/") or ".." in path.split("/"):
            return {"ok": False, "path": path, "error": "Invalid path"}
        try:
            content = await _gh_fetch_file(owner, repo, path, branch, token)
            if content is None:
                return {"ok": False, "path": path, "error": f"`{path}` not found on {branch}"}
            if _is_binary_data(content):
                return {
                    "ok": False,
                    "path": path,
                    "error": "Binary data detected - direct chat input must be text only",
                    "status": 415,
                }
            content, truncated = _slice_content(content, line_range, MAX_FILE_CHARS)
            return {"ok": True, "path": path, "content": content, "truncated": truncated}
        except GithubAuthError as e:
            return {**_github_access_revoked_error(owner, repo, e.status_code), "path": path}
        except Exception as e:
            return {"ok": False, "path": path, "error": str(e)}

    results = await asyncio.gather(*[_fetch_one(p) for p in paths])

    ok_files = [r for r in results if r.get("ok")]
    err_files = [r for r in results if not r.get("ok")]

    failure_warning = None
    if len(paths) >= 3 and len(err_files) >= max(2, len(paths) // 2):
        failure_warning = (
            f"⚠️ HALLUCINATION RISK - {len(err_files)}/{len(paths)} of the "
            "paths you guessed do not exist in this repo. STOP. Your next "
            "tool call MUST be `list_repo_files` with a wide glob (e.g. "
            "`**/*.py`, `backend/**`, `**/auth*`) to see the ACTUAL layout. "
            "Do not write a plan or handoff brief until you have seen real "
            "paths. The repo's structure is different from what you "
            "expected - that's normal, every codebase is unique."
        )

    warning_lines: list[str] = []
    if failure_warning:
        warning_lines.append(failure_warning)
    if _dropped:
        warning_lines.append(
            f"⚠️ read_repo_files HARD-CAPS at {MAX_FILES_BULK} paths per "
            f"call. {len(_dropped)} path(s) were NOT fetched: "
            + ", ".join(_dropped)
            + ". Call `read_repo_file` (singular) in separate blocks "
            "for the dropped ones, or split the bulk call into batches."
        )
    combined_warning = "\n\n".join(warning_lines) if warning_lines else None

    return {
        "ok":      len(ok_files) > 0,
        "branch":  branch,
        "files":   list(results),
        "fetched": len(ok_files),
        "requested": len(_raw_paths),
        "dropped": _dropped,
        "errors":  [f"{r['path']}: {r.get('error','?')}" for r in err_files],
        **({"warning": combined_warning} if combined_warning else {}),
    }

async def write_repo_file(ctx: dict, args: dict) -> dict:
    user_id = ctx.get("user_id")
    project_id = ctx.get("project_id")
    path = (args or {}).get("path")
    content = (args or {}).get("content")
    commit_msg = (args or {}).get("commit_message") or f"chore: edit {path}"

    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing required arg `path`."}
    if path.startswith("/") or ".." in path.split("/"):
        return {"ok": False, "error": "Invalid path - no absolute paths or traversal."}
    if not _is_safe_repo_path(path):
        return {"ok": False, "error": (
            "Invalid path - filenames may only contain letters, digits, "
            "spaces, dots, dashes, underscores, and `/` as a path "
            "separator. No shell/special characters."
        )}
    if not isinstance(content, str):
        return {"ok": False, "error": "Arg `content` must be a string (full file body)."}
    if _is_binary_data(content):
        return {
            "ok": False,
            "error": (
                "Binary data detected in content - direct chat input must "
                "be UTF-8 text only. For binary files, use proper file "
                "uploads instead of direct input."
            ),
            "status": 415,
        }
    if len(content) > 200_000:
        return {"ok": False,
                "error": "File body exceeds 200KB cap - split into smaller files."}

    try:
        from .loop_diff_classifier import is_test_or_fixture
    except Exception:
        is_test_or_fixture = lambda _p: False
    if is_test_or_fixture(path) and not (args or {}).get("allow_test_file_change"):
        return {
            "ok": False,
            "error": (
                "test-file write blocked - test files are protected "
                "by the loop-pipeline test-file lock. Route this "
                "change through Loop mode so the held-out verifier "
                "reviews it, or ask a human maintainer to set "
                "allow_test_file_change=true in an approved patch."
            ),
            "gate":  "test_file_lock",
            "path":  path,
        }

    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, branch, token = rc["owner"], rc["repo"], rc["branch"], rc["token"]
    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo."}
    if not token:
        return {
            "ok": False,
            "error": (
                "GitHub App access is unavailable for this project - "
                "write_repo_file needs write access. Reconnect the repo "
                "via Projects → APP (AUREM GitHub App)."
            ),
        }

    try:
        from core.errors import WriteGuardBlockedError
        from services.write_guard import check_write_paths as _check_write_paths
        from cto_services.db import get_db as _get_guard_db
        await _check_write_paths(_get_guard_db(), [path], owner=owner, repo=repo, branch=branch)
    except WriteGuardBlockedError as _wg_err:
        return {"ok": False, "error": str(_wg_err), "gate": "write_guard", "path": path}
    except Exception:
        pass

    _contrast_note = None
    if re.search(r"\.(css|scss)$", path, re.IGNORECASE) or "theme" in path.lower():
        try:
            from services.contrast_guard import check_and_nudge_css, describe_nudge
            _cg = check_and_nudge_css(content)
            if _cg["adjustments"]:
                content = _cg["content"]
                _contrast_note = _cg["adjustments"]
        except Exception:
            pass

    return {
        "ok":      True,
        "query":   query,
        "total":   data.get("total_count", 0) or len(results),
        "results": results[:max_hits],
        "hint": (
            f"Found {len(results)} files. "
            "Use read_repo_files to read the most relevant ones in parallel."
        ),
    }


async def _index_tfidf_search(query: str, user_id: str,
                              project_id: str, max_hits: int) -> list[dict]:
    """Bag-of-words overlap scorer over the cached codebase index.

    Returns at most `max_hits` results sorted by descending score.
    Empty list on any error so callers can no-op the fallback.
    """
    if max_hits <= 0:
        return []
    try:
        from cto_services.db import get_db as _gdb
        db = _gdb()
        if db is None:
            return []
        idx = await db.cto_codebase_index.find_one(
            {"user_id": user_id, "project_id": project_id},
            {"files": 1, "_id": 0},
        )
        if not idx or not idx.get("files"):
            return []
        q_words = {w for w in query.lower().split() if len(w) > 2}
        if not q_words:
            return []
        scored: list[dict] = []
        for f in idx["files"]:
            blob = (
                (f.get("snippet") or "")
                + " " + (f.get("path") or "")
                + " " + (f.get("role") or "")
            ).lower()
            overlap = sum(1 for w in q_words if w in blob)
            if overlap:
                scored.append({
                    "path":   f.get("path") or "",
                    "score":  round(overlap / max(len(q_words), 1), 2),
                    "source": "index_tfidf",
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:max_hits]
    except Exception as _e:
        logger.debug("[silent-catch] services/local_tools.py:1678 in _index_tfidf_search — %r", _e)
        return []


# ── TOOL 6: get_commit_diff (single-commit patch) ────────────────────────────

async def get_commit_diff(ctx: dict, args: dict) -> dict:
    """Return the diff for one commit.

    Pairs with the brain context's "recent commits" so the model can see
    exactly HOW similar work was done before writing new code.

    args:
      sha  str  — commit SHA (7 or 40 chars)
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    sha        = ((args or {}).get("sha") or "").strip()

    if not sha:
        return {"ok": False,
                "error": "sha is required (get it from brain context recent commits)"}

    # Iter 212m-169 — BINContext gate.
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, token = rc["owner"], rc["repo"], rc["token"]
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner or github_repo"}

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    import httpx
    from services.http import ext_client
    try:
        async with ext_client(
            "github",
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        ) as c:
            r = await c.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}",
                headers=headers,
            )
            if r.status_code == 404:
                return {"ok": False, "error": f"Commit {sha} not found"}
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"GitHub API error: {e}"}

    files_changed = [
        {
            "path":      f["filename"],
            "status":    f["status"],
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "patch":     (f.get("patch") or "")[:600],
        }
        for f in (data.get("files") or [])[:8]
    ]
    commit_info = data.get("commit", {}) or {}
    author = commit_info.get("author") or {}
    return {
        "ok":           True,
        "sha":          sha[:7],
        "message":      commit_info.get("message", ""),
        "author":       author.get("name", ""),
        "date":         author.get("date", ""),
        "files_changed": files_changed,
        "total_files":   len(data.get("files") or []),
    }


# ── TOOL 7: get_repo_info (project metadata) ─────────────────────────────────

async def get_repo_info(ctx: dict, args: dict) -> dict:
    """Return connected project metadata: owner, repo, branch, tech_stack, last task."""
    # Iter 212m-169 — BINContext gate.  This tool is read-only project
    # metadata so we surface owner/repo/branch straight from bin_ctx.
    # Extra metadata (tech_stack, last_task) is fetched from the DB
    # ONLY after ownership is proven by the presence of a valid
    # BINContext for this user (bin_ctx.pid + bin_ctx.bin_id).
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    db = get_db()
    extra: dict = {}
    if db is not None:
        try:
            proj = await db.cto_projects.find_one(
                {"project_id": rc["pid"], "user_id": rc["bin_id"]},
                {"_id": 0, "name": 1, "tech_stack": 1, "last_task": 1,
                 "tasks_done": 1},
            )
            if proj:
                extra = {
                    "name":       proj.get("name"),
                    "tech_stack": proj.get("tech_stack", "unknown"),
                    "last_task":  proj.get("last_task"),
                    "tasks_done": proj.get("tasks_done", 0),
                }
        except Exception as e:                       # noqa: BLE001
            logger.debug("get_repo_info metadata lookup failed: %r", e)
    return {
        "ok":           True,
        "project_id":   rc["pid"],
        "github_owner": rc["owner"],
        "github_repo":  rc["repo"],
        "branch":       rc["branch"],
        "has_pat":      bool(rc["token"]),
        **extra,
    }


# ── TOOL: save_finding (persist a conversational audit finding) ─────────────
#
# 2026-08-22 — closes a real gap: when a user asks "check my code for
# security problems" (or any bug/quality audit) conversationally, ORA
# would previously just write a nice markdown report (## CRITICAL /
# ## HIGH / ...) straight into the chat reply — and nothing was ever
# saved. The report vanished into scroll-back with no way to track,
# revisit, or get reminded about those issues later. `cto_open_findings`
# already exists and already backs the reminder banner
# (ScanStatusStrip.jsx) + the Prompt Starter panel's "FROM YOUR REPO"
# suggestions — it was just never fed by conversational audits, only
# by the automated scanner pipelines. This tool lets ORA write ONE
# finding at a time into that same real backlog while it narrates the
# audit, using the exact same persistence helper the scanners use.
async def _verify_chat_finding(
    *, rc: dict, file_: str, line_: int,
) -> tuple[bool, str, str | None]:
    """Robot Fix List B2 — real-only gate. Returns
    (verified, verified_reason, normalized_category).

    A chat-audit finding is verified ONLY when: the file exists in the
    repo at the project's branch, the claimed line is within the
    file's length, AND the static scanner (services.vanguard_scanner)
    independently detects SOMETHING within 2 lines of the claim —
    `normalized_category` is that scanner hit's own rule name, used
    for the canonical id instead of the LLM's free-text title (so the
    same real issue, described two different ways, collapses to one
    id). Never raises — any failure degrades to unverified, never to
    "verified" by default."""
    if not file_:
        return False, "no_file_specified", None
    if line_ <= 0:
        return False, "no_line_specified", None
    try:
        from services.finding_fix_applier import _fetch_file_content
        content, err = await _fetch_file_content(
            rc["owner"], rc["repo"], rc["branch"], file_, rc["token"],
        )
    except Exception as e:                                # noqa: BLE001
        logger.warning("save_finding verify fetch failed: %r", e)
        return False, "repo_unreadable", None
    if err:
        reason = "file_not_found" if err == "file_not_found" else "repo_unreadable"
        return False, reason, None
    total_lines = content.count("\n") + 1
    if line_ > total_lines:
        return False, "line_out_of_range", None
    try:
        from services.vanguard_scanner import scan_text
        hits = scan_text(content, file_) or []
    except Exception as e:                                # noqa: BLE001
        logger.warning("save_finding verify scan failed: %r", e)
        return False, "scan_failed", None
    for h in hits:
        h_line = h.get("line") or 0
        if abs(int(h_line) - line_) <= 2:
            category = h.get("name") or h.get("rule") or ""
            if category:
                return True, "", category
    return False, "not_detected_by_scanner", None


async def save_finding(ctx: dict, args: dict) -> dict:
    """Persist one security/bug/quality finding discovered during a
    conversational audit into the user's real findings backlog (the
    same store the automated scanners write to) — call this once per
    finding whenever you report a CRITICAL/HIGH/MEDIUM/LOW audit
    result in chat, so it can be tracked and the user can be reminded
    later instead of it only existing as chat text."""
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR

    a = args or {}
    title = (a.get("title") or "").strip()
    severity = (a.get("severity") or "").strip().lower()
    if not title:
        return {"ok": False, "error": "title is required"}
    if severity not in ("critical", "high", "medium", "low"):
        return {"ok": False, "error": "severity must be one of: critical, high, medium, low"}

    file_ = (a.get("file") or "").strip()
    try:
        line_ = int(a.get("line") or 0)
    except (TypeError, ValueError):
        line_ = 0

    db = get_db()
    if db is None:
        return {"ok": False, "error": "database unavailable"}

    # Robot Fix List B1/BE2 — resolve the SAME effective account
    # fix_list reads (team member -> owner's shared id), not the
    # caller's own raw bin_id. Without this, a team member's own
    # chat-saved findings were written under an id fix_list never
    # queries and silently never appeared.
    from services.finding_scope import resolve_findings_scope
    scope = await resolve_findings_scope(
        db, caller_user_id=rc["bin_id"], project_id=rc["pid"], level="write",
    )
    effective_user_id = scope["user_id"]

    verified, verified_reason, normalized_category = await _verify_chat_finding(
        rc=rc, file_=file_, line_=line_,
    )
    rule_slug = (
        normalized_category
        if verified and normalized_category
        else (re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "finding")
    )

    # B2 — a scanner-origin finding already covering this exact
    # file+line+category wins; the chat-audit duplicate is dropped
    # rather than creating a second row for the same real issue.
    if verified and normalized_category:
        existing_scanner_row = await db.cto_open_findings.find_one({
            "user_id": effective_user_id, "project_id": rc["pid"],
            "file": file_, "line": line_, "status": "open",
            "scanner": {"$ne": "ora_chat_audit"},
            "rule_id": normalized_category,
        })
        if existing_scanner_row:
            return {
                "ok": True, "saved": False, "severity": severity,
                "finding_id": existing_scanner_row.get("finding_id"),
                "file": file_, "line": line_,
                "note": "Already tracked by an automated scan — not duplicated.",
            }

    # 2026-10 — C3 fix. `a.get("description")`/`a.get("fix_hint")` is
    # whatever free text the model wrote — when the user pasted code
    # containing a REAL secret and asked ORA to review it, the model
    # would often restate that raw value verbatim in its own
    # description, and this tool stored it exactly as written. Mask
    # before it ever reaches `finding` (so every downstream reader —
    # Fix List, the mascot, /findings — gets the masked version, not
    # just the persisted row).
    from services.secret_masking import redact_secrets_in_text
    finding = {
        "scanner":  "ora_chat_audit",
        "id":       rule_slug,
        "severity": severity,
        "file":     file_,
        "line":     line_,
        "title":    redact_secrets_in_text(title[:200]),
        "message":  redact_secrets_in_text((a.get("description") or "")[:500]),
        "fix_hint": redact_secrets_in_text((a.get("fix_hint") or "")[:300]),
        "verified": verified,
        "verified_reason": "" if verified else verified_reason,
    }

    from services.loop_full_scan import persist_findings_to_backlog
    try:
        written = await persist_findings_to_backlog(
            db, user_id=effective_user_id, project_id=rc["pid"],
            findings=[finding], scan_source="ora_chat_audit",
        )
    except Exception as e:                             # noqa: BLE001
        logger.warning("save_finding persist failed: %r", e)
        return {"ok": False, "error": "failed to save finding"}

    return {
        "ok":       True,
        "saved":    written > 0,
        "severity": severity,
        "verified": verified,
        # 2026-08-23 — findings-to-fix bridge. Same deterministic id
        # formula as persist_findings_to_backlog (loop_full_scan.py)
        # so orchestrator.py can track this finding and, later, the
        # fix pipeline can write back "resolved" against the exact
        # same row.
        "finding_id": f"ora_chat_audit::{file_}:{line_}:{rule_slug}",
        "file":     file_,
        "line":     line_,
        "title":    finding["title"],
        "message":  finding["message"],
        "fix_hint": finding["fix_hint"],
        "note": (
            "Recorded, but could not be independently verified "
            f"({verified_reason}) — it won't appear in the Fix List "
            "or suggestions until it is."
            if not verified else
            "Saved to the user's findings backlog — it will surface in "
            "their reminder strip and personalized suggestions."
            if severity in ("critical", "high") else
            "Recorded. Note: medium/low severity findings don't surface "
            "on the reminder strip by design — only critical/high do."
        ),
    }


# ── TOOL 8: execute_bash (read-only local pod filesystem) ────────────────────
#
# Iter 138 — closes the "ORA can't inspect /app/ files" gap that caused
# the model to hallucinate file contents or mis-use the aurem-handoff
# fence whenever the user asked to run a literal terminal command
# (`cat /app/...`, `find /app/backend/...`). The earlier catalog only
# had GitHub-API tools, so anything on the LOCAL pod filesystem was
# inaccessible. We now ship a tightly-scoped, read-only shell runner.
#
# Safety contract (do NOT relax without an audit):
#   - Allowlist of binaries (no shell builtins, no chained `; rm`)
#   - 15 s wall-clock cap
#   - 8 KB stdout / 1 KB stderr cap to prevent context bloat
#   - No env passthrough required — inherits the worker's env so
#     paths like /app are reachable but no extra secrets get exposed
#   - SEC-001 fix (2026-01-22): runs via `create_subprocess_exec` with
#     an argv list (NO shell). Any shell metacharacter (`|`,`;`,`&`,
#     `` ` ``,`$`,`>`,`<`) is hard-rejected before parsing. Pipes/
#     redirects/chaining/substitution are intentionally NOT supported
#     — a prior version gated only the first token then ran the full
#     string via `create_subprocess_shell`, which let a piped/chained
#     non-allowlisted command (e.g. `cat foo | rm -rf /`) execute.
_BASH_ALLOWED = {
    "cat", "head", "tail", "grep", "find", "ls", "wc",
    "sed", "awk", "echo", "pwd", "stat", "tree", "file",
    "which", "whereis", "basename", "dirname", "sort", "uniq",
    "cut", "tr", "true", "false",
}


async def execute_bash(ctx: dict, args: dict) -> dict:
    """Run a READ-ONLY bash command on the local pod filesystem.

    args:
      command: str — the bash command (first token must be allowlisted)

    SECURITY (Iter 212m-168): This tool exposes the local pod
    filesystem (`/app`, `/tmp`, `/var/log`, etc.) — which contains
    the internal AUREM codebase.  It MUST NOT be exposed to
    end-user (customer) chat sessions or the LLM will surface AUREM
    internal paths when the user asks about *their* connected repo
    ("which repo are you working on?" → LLM inspects /app/backend and
    reports auremcto internals — privacy + correctness bug).

    Only founder/admin sessions (ctx["is_founder"] is True) are
    allowed to invoke this tool.  Regular users see a clear refusal
    that redirects them to the GitHub-scoped tools.
    """
    import asyncio
    import shlex

    # Iter 212m-168 — HARD gate.  ctx.is_founder is populated by the
    # orchestrator from the authenticated user's role.  Anonymous /
    # regular / paid users never see this tool in the catalog either
    # (see orchestrator.py filter), but this belt-and-braces check
    # blocks any LLM that hallucinates the tool name from succeeding.
    #
    # Iter 212m-169 — Additional BINContext defence.  When a BINContext
    # is present (project-scoped chat), we ALSO require bin_ctx.is_founder.
    # This closes the theoretical bypass where a founder starts a chat
    # session (ctx.is_founder=True from JWT) but is currently scoped to
    # a project marked non-founder in bin_ctx — we defer to the stricter
    # of the two.  When there is no bin_ctx (Home chat), we fall back
    # to ctx.is_founder alone.
    if not bool(ctx.get("is_founder")):
        return {
            "ok": False,
            "error": (
                "execute_bash is restricted to founder/admin accounts. "
                "For the user's connected repo, use `read_repo_file`, "
                "`read_repo_files`, `list_repo_files`, `search_repo`, "
                "or `semantic_search_repo` — those are the ONLY tools "
                "that read the user's own GitHub repo.  Never inspect "
                "local pod paths (/app, /tmp, /var, /etc, /usr) — "
                "those are internal AUREM server paths, not the user's "
                "codebase."
            ),
        }
    _bc = (ctx or {}).get("bin_ctx")
    if _bc is not None and not bool(getattr(_bc, "is_founder", False)):
        return {
            "ok": False,
            "error": (
                "execute_bash is restricted to founder/admin accounts. "
                "Use GitHub-scoped tools instead."
            ),
        }

    cmd = (args or {}).get("command", "").strip()
    if not cmd:
        return {"ok": False, "error": "command is required"}

    # Iter 212m-170 — ORA ABSOLUTE BOUNDARY on execute_bash args.
    # Even for founders, /app/*, /tmp/*, /var/*, /etc/*, /usr/*,
    # /root/*, /home/* paths are OFF-LIMITS unless the founder has
    # explicitly enabled debug_mode on their ORAContext (which is
    # itself gated on is_founder=True at build time).  This means
    # the DEFAULT founder session cannot inspect the AUREM pod
    # filesystem — they must opt-in to debug_mode first (via the
    # admin panel toggle, tracked in ORAContext.debug_mode).
    #
    # Iter 388t — Bug 20 fix.  Founder-pod-debug mode (is_founder
    # AND no project attached) is a SECOND escape hatch specifically
    # for the founder's own no-project workspace.  It runs the
    # additional `validate_founder_pod_command` safety check
    # (no `;`/`&&`/`||` chaining, no `..` traversal, path args
    # confined to /app, /tmp, /var, /etc, /usr and outside the
    # secret denylist) BEFORE lifting the ora-boundary refusal.
    #
    # Non-founder sessions never reach this point (blocked above),
    # but the boundary check runs anyway as defence-in-depth.
    from services.ora_context import (
        path_hits_ora_boundary,
        validate_founder_pod_command,
    )
    _hit = path_hits_ora_boundary(cmd)
    if _hit is not None:
        # Founder + debug_mode → allow.  Founder + pod-mode (no
        # project) → allow after the founder-pod command validator
        # accepts the command.  Every other combination refuses.
        _founder_pod = bool(ctx.get("founder_pod_mode"))
        allow = (
            _bc is not None
            and bool(getattr(_bc, "is_founder", False))
            and bool(getattr(_bc, "debug_mode", False))
        )
        if not allow and _founder_pod:
            # Run the extra safety validator before unlocking.  If
            # the founder's command trips a rule (chaining, traversal,
            # path outside allowlist, secret denylist), we refuse
            # here with a specific reason so the founder can fix it.
            _ok, _reason = validate_founder_pod_command(cmd)
            if _ok:
                allow = True
            else:
                return {
                    "ok": False,
                    "error": (
                        f"execute_bash refused (founder-pod-mode safety): {_reason}"
                    ),
                    "error_class": "founder_pod_validation",
                }
        if not allow:
            # Iter 212m-171 — log to audit_log so /admin/boundary-probes
            # can surface it as an admin overview tile.
            try:
                from cto_services.db import get_db
                from datetime import datetime, timezone
                _db = get_db()
                if _db is not None:
                    await _db.audit_log.insert_one({
                        "ts": datetime.now(timezone.utc),
                        "event": "ora_boundary_violation",
                        "user_id": ctx.get("user_id"),
                        "is_founder": bool(getattr(_bc, "is_founder", False))
                                      if _bc else False,
                        "cmd_head": cmd[:120],
                        "hit": _hit,
                    })
            except Exception as _e:
                logger.debug("[silent-catch] services/local_tools.py:1922 in execute_bash — %r", _e)
                pass
            return {
                "ok": False,
                "error": (
                    f"execute_bash refused: the command references "
                    f"ORA-internal path `{_hit}` which is OFF-LIMITS in "
                    f"normal mode.  If you are a founder developing "
                    f"AUREM itself, enable Debug Mode in the admin "
                    f"panel and start a new chat session."
                ),
                "error_class": "ora_boundary_violation",
            }


    # SEC-001 fix (audit 2026-01-22) — the old gate only checked the
    # FIRST token against the allowlist but still ran the WHOLE string
    # via create_subprocess_shell. `cat foo | rm -rf /` passed the gate
    # (first token "cat") yet the shell happily executed the piped
    # `rm`. Same hole for `&&`, `;`, `$(...)`, backticks, `>` redirects.
    # Fix: (1) hard-reject any shell metacharacter outright — this is
    # a read-only inspection tool, it never needs pipes/redirects/
    # substitution/chaining; (2) run via create_subprocess_exec with
    # an argv list so there is no shell to interpret metacharacters
    # even if one slipped through.
    _SHELL_METACHARS = set('|;&`$><\n')
    if any(ch in cmd for ch in _SHELL_METACHARS):
        return {
            "ok": False,
            "error": (
                "execute_bash refused: pipes, redirects, command "
                "chaining (`|`, `;`, `&&`, `>`, `$()`, backticks) are "
                "not permitted. Run one plain read-only command at a "
                "time, e.g. `grep -rn pattern dir` (use its own flags "
                "instead of piping to `head`)."
            ),
            "error_class": "shell_metachar_blocked",
        }

    # Parse first token to gate against the allowlist. We use shlex so
    # quoted paths don't trip the parser.
    try:
        tokens = shlex.split(cmd)
        first_word = tokens[0]
    except (ValueError, IndexError) as e:
        return {"ok": False, "error": f"shell parse error: {e}"}

    # Strip a leading path so the LLM can write `/usr/bin/cat …` too.
    binary = first_word.rsplit("/", 1)[-1]
    if binary not in _BASH_ALLOWED:
        return {
            "ok": False,
            "error": (
                f"Command '{binary}' not allowed. Only read-only "
                f"commands permitted: {', '.join(sorted(_BASH_ALLOWED))}."
            ),
        }

    # 2026-09-30 — SECURITY FIX (Medium, code-read audit): this tool is
    # documented above as "read-only local pod filesystem" (see the
    # "TOOL 8" banner), but `sed -i`/`--in-place` writes files in place
    # and `find ... -delete`/`-exec`/`-execdir`/`-ok`/`-okdir`/`-fprint*`
    # can delete or overwrite arbitrary files the pod process can reach
    # — neither was actually blocked, only implied by the comment.
    # `create_subprocess_exec` (below) already means no shell is
    # involved, so this only needs to catch the primaries/flags that
    # give sed/find a write/delete side effect; every other sed/find
    # use (the vast majority — filtering, printing, searching) stays
    # exactly as capable as before.
    def _sed_is_inplace(tok: str) -> bool:
        if tok.startswith("--in-place"):
            return True
        if tok.startswith("--") or not tok.startswith("-") or len(tok) < 2:
            return False
        # Single-dash short-option cluster (GNU getopt lets sed's flags
        # combine, e.g. `-ni` == `-n -i`) — 'i' anywhere in the cluster
        # means in-place, regardless of position or an attached backup
        # suffix (`-i.bak`).
        return "i" in tok[1:]

    _DANGEROUS_FLAGS = {
        "sed": _sed_is_inplace,
        "find": lambda tok: tok in (
            "-delete", "-exec", "-execdir", "-ok", "-okdir",
            "-fprintf", "-fprint", "-fprint0", "-fls",
        ),
    }
    _flag_is_dangerous = _DANGEROUS_FLAGS.get(binary)
    if _flag_is_dangerous and any(_flag_is_dangerous(t) for t in tokens[1:]):
        return {
            "ok": False,
            "error": (
                f"execute_bash refused: '{binary}' was used with a "
                f"flag that writes, deletes, or executes rather than "
                f"reads (e.g. sed -i, find -delete/-exec). This tool "
                f"is read-only — use it without that flag, or use the "
                f"proper write tool (edit_repo_file / write_repo_file) "
                f"for an actual change."
            ),
            "error_class": "write_flag_blocked",
        }

    try:
        from services.commit_files import commit_files
        result = await commit_files(
            owner, repo, branch, token,
            {path: content}, commit_msg,
        )
        if result.get("ok"):
            _cache_invalidate(project_id, path)
            if _contrast_note:
                result["contrast_nudges"] = _contrast_note
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}
