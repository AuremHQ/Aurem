"""
services/github_repo_fetch.py — read-only GitHub repo tree / blob fetch
helpers (+ scan constants). Extracted verbatim from routers/security_scan.py
(2026-09-07) so services (codebase_health text-cache, onboarding scan)
stop importing the router layer. routers/security_scan.py re-exports
every symbol under its old private name, so callers/tests are unchanged.
"""
from __future__ import annotations

import base64
import logging

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# File extensions worth scanning. Skipping binary, vendored, build.
_SCAN_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".env", ".env.example",
    ".yml", ".yaml", ".json", ".sh", ".sql", ".php", ".rb", ".go",
}
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next",
              "venv", ".venv", "__pycache__", ".cache", "coverage"}
_MAX_FILES = 600              # safety cap for huge repos
_MAX_BYTES_PER_FILE = 256_000  # skip files larger than 256 KB
_GH_API = "https://api.github.com"
_GH_TIMEOUT = 20.0
_CONCURRENT_FETCHES = 8        # parallel raw-content fetches


def _gh_headers(pat: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {pat}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _gh_get(client: httpx.AsyncClient, url: str, pat: str):
    """GitHub GET with meaningful error propagation.

    Iter 212m-216 — Before this iter, ANY non-200 that wasn't 401/
    404/5xx (notably 403 secondary rate-limits and 422 empty-repo
    errors) hit `r.raise_for_status()`, which threw an
    `HTTPStatusError`.  That was then caught by the outer wrap in
    `codebase_health.scan()` (`except Exception → HTTPException(502)`),
    and Cloudflare's 5xx intercept replaced our JSON body with a
    branded HTML "Bad gateway" page.  Users saw a raw 1.3s 502 with
    no clue why — the actual reason (rate limit, empty repo, etc.)
    never reached the browser.

    We now branch on every meaningful GH status explicitly.  Rate
    limits become 429 with `retry_after` so the frontend can back
    off.  Empty / mis-branched repos surface 422.  Auth / permission
    failures surface 401/403 with the real GitHub reason.  Only
    genuine upstream 5xx bubbles as 502.
    """
    try:
        r = await client.get(url, headers=_gh_headers(pat), timeout=_GH_TIMEOUT)
    except httpx.TimeoutException as e:
        raise HTTPException(504, f"github_upstream_timeout: {e!s}")
    except httpx.RequestError as e:
        # DNS / TLS / connection reset — never our fault, never the
        # caller's fault.  Bubble as 502 with the real error class so
        # a founder can debug from prod logs.
        raise HTTPException(502, f"github_transport_{type(e).__name__}: {e!s}")

    sc = r.status_code
    if sc == 200:
        try:
            return r.json()
        except Exception as e:
            raise HTTPException(502, f"github_bad_json: {e!s}")

    # ── Meaningful GH statuses — surface the actual reason ─────────
    # Extract GitHub's own error message so the client sees
    # `"detail": "github_rate_limited: API rate limit exceeded ..."`
    # not a blanket "Bad gateway".
    gh_msg = ""
    try:
        j = r.json()
        gh_msg = (j.get("message") or "")[:200]
    except Exception:
        gh_msg = (r.text or "")[:200]

    if sc == 401:
        raise HTTPException(401, f"github_pat_invalid: {gh_msg}"
                            if gh_msg else "github_pat_invalid")
    if sc == 403:
        # 403 on GH is nearly always a rate limit (primary or
        # secondary) or SSO-restricted org.  Distinguish so the UI
        # can render the right toast.
        remaining = r.headers.get("x-ratelimit-remaining")
        reset     = r.headers.get("x-ratelimit-reset")
        if remaining == "0" and reset:
            try:
                import time as _t
                retry_after = max(1, int(reset) - int(_t.time()))
            except Exception:
                retry_after = 60
            raise HTTPException(429, {
                "error":               "github_rate_limited",
                "message":             f"GitHub API rate limit exhausted. "
                                        f"Retry in ~{retry_after}s.",
                "retry_after_seconds": retry_after,
                "github_message":      gh_msg,
            })
        # Secondary rate limit or org-restricted PAT
        raise HTTPException(403, f"github_forbidden: {gh_msg}"
                            if gh_msg else "github_forbidden")
    if sc == 404:
        raise HTTPException(404, f"github_repo_not_found: {gh_msg}"
                            if gh_msg else "github_repo_not_found")
    if sc == 409:
        # Empty repo — /git/trees fails with 409 "Git Repository is empty"
        raise HTTPException(422, f"github_repo_empty: {gh_msg}"
                            if gh_msg else "github_repo_empty")
    if sc == 422:
        # Bad ref, missing branch, or default_branch missing.
        raise HTTPException(422, f"github_bad_ref: {gh_msg}"
                            if gh_msg else "github_bad_ref")
    if sc == 451:
        raise HTTPException(451, f"github_unavailable_for_legal: {gh_msg}"
                            if gh_msg else "github_unavailable_for_legal")
    if sc >= 500:
        raise HTTPException(502, f"github_upstream_{sc}: {gh_msg}"
                            if gh_msg else f"github_upstream_{sc}")
    # Anything else — treat as an actionable client error, but include
    # the exact code so we can debug from a screenshot.
    raise HTTPException(sc if 400 <= sc < 500 else 502,
                        f"github_unexpected_{sc}: {gh_msg}"
                        if gh_msg else f"github_unexpected_{sc}")


async def _list_repo_tree(client: httpx.AsyncClient, owner: str, repo: str, pat: str) -> list[dict]:
    """One GitHub API hit for tree; we cap at _MAX_FILES below."""
    blobs, _sha = await _list_repo_tree_with_sha(client, owner, repo, pat)
    return blobs


async def _list_repo_tree_with_sha(
    client: httpx.AsyncClient, owner: str, repo: str, pat: str,
) -> tuple[list[dict], str]:
    """Same as `_list_repo_tree` but also returns the GitHub tree SHA
    so callers can key a content-addressed cache.  Iter 212m-79."""
    repo_meta = await _gh_get(client, f"{_GH_API}/repos/{owner}/{repo}", pat)
    branch = repo_meta.get("default_branch") or "main"
    tree = await _gh_get(
        client,
        f"{_GH_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
        pat,
    )
    sha = tree.get("sha") or ""
    blobs = [t for t in (tree.get("tree") or []) if t.get("type") == "blob"]
    return blobs, sha


async def _fetch_file(
    client: httpx.AsyncClient, owner: str, repo: str, path: str, pat: str,
) -> str:
    """Fetch raw content via contents API. Returns "" on failure so the
    scan keeps going for the rest of the repo."""
    try:
        r = await client.get(
            f"{_GH_API}/repos/{owner}/{repo}/contents/{path}",
            headers=_gh_headers(pat), timeout=_GH_TIMEOUT,
        )
        if r.status_code != 200:
            return ""
        body = r.json()
        if body.get("encoding") == "base64" and body.get("content"):
            return base64.b64decode(body["content"]).decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug("fetch failed %s: %r", path, e)
    return ""
