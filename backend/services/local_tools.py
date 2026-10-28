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
