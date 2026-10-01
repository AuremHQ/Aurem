"""
services/checkup_history.py — look for credentials committed in the recent
git history (L7.2), including ones deleted since: deleting a leaked key
from the latest code does not un-leak it.

Read-only, only on repos the user connected.  Patches are scanned in
memory; what leaves this module is (short commit id, file, credential
TYPE) — never the value (S8).  Bounded to the newest `MAX_COMMITS`
commits so a checkup stays fast; that bound is stated in the check copy.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from services.checkup_common import _is_vendored
from services.checkup_secret_patterns import find_secrets

logger = logging.getLogger(__name__)

MAX_COMMITS = 20
_CONCURRENCY = 4
_SKIP_SUFFIXES = (".lock", ".min.js", ".map", "package-lock.json")


def _scannable(path: str) -> bool:
    return bool(path) and not _is_vendored(path) and not path.endswith(_SKIP_SUFFIXES)


def _added_text(patch: str) -> str:
    return "\n".join(ln[1:] for ln in patch.splitlines()
                     if ln.startswith("+") and not ln.startswith("+++"))


def secrets_in_patch(files: list[dict]) -> list[tuple[str, str]]:
    """[(path, type)] for credentials on ADDED lines of one commit's files."""
    out: list[tuple[str, str]] = []
    for f in files or []:
        path = f.get("filename") or ""
        if _scannable(path):
            kinds = {kind for kind, _ in find_secrets(_added_text(f.get("patch") or ""))}
            out.extend((path, k) for k in sorted(kinds))
    return out


async def _commit_findings(client, base: str, sha: str, headers: dict, sem) -> list[dict]:
    async with sem:
        try:
            r = await client.get(f"{base}/commits/{sha}", headers=headers, timeout=15.0)
            if r.status_code != 200:
                return []
            files = r.json().get("files") or []
        except (httpx.HTTPError, ValueError) as e:
            logger.debug("history commit fetch failed %s: %r", sha[:7], e)
            return []
    return [{"commit": sha[:7], "path": p, "type": k} for p, k in secrets_in_patch(files)]


async def scan_history(owner: str, repo: str, pat: str) -> list[dict]:
    """Raises on a failed commit listing so the check reads as unverified."""
    from services.github_repo_fetch import _GH_API, _gh_get, _gh_headers
    from services.http import ext_client

    base = f"{_GH_API}/repos/{owner}/{repo}"
    async with ext_client("github", timeout=httpx.Timeout(40.0, connect=6.0, read=15.0)) as client:
        commits = await _gh_get(client, f"{base}/commits?per_page={MAX_COMMITS}", pat)
        sem = asyncio.Semaphore(_CONCURRENCY)
        headers = _gh_headers(pat)
        batches = await asyncio.gather(*[
            _commit_findings(client, base, c["sha"], headers, sem)
            for c in commits if c.get("sha")])
    return [f for b in batches for f in b]
