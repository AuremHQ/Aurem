"""
services/checkup_meta.py — repo-level facts the checkup checks need that
the `{path: text}` cache can't carry: the full file list (including files
we never read, like `.env.production`) and a few small config files
(`.gitignore`).

They travel inside the same mapping under a reserved key prefix so every
check keeps the `(tc) -> items` signature.  `strip_meta` hands scanners
and the permission map the plain file cache they have always received.

A check that needs a fact we couldn't fetch must raise `MetaUnavailable`:
the orchestrator then reports it as "couldn't verify" instead of letting
a missing fact read as "all clear" (S9).
"""
from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

META_PREFIX = "::meta::"
_PATHS_KEY = META_PREFIX + "paths"
# Small files worth reading even though the text cache skips them.
META_FILES = (".gitignore",)
_MAX_META_BYTES = 64_000


class MetaUnavailable(Exception):
    """The repo fact a check depends on was not fetched."""


def is_meta_key(key: str) -> bool:
    return key.startswith(META_PREFIX)


def strip_meta(tc: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in tc.items() if not is_meta_key(k)}


def with_meta(tc: dict[str, str], paths: Iterable[str], files: dict[str, str]) -> dict[str, str]:
    out = dict(tc)
    out[_PATHS_KEY] = "\n".join(p for p in paths if p)
    for name, text in files.items():
        out[META_PREFIX + "file:" + name] = text[:_MAX_META_BYTES]
    return out


def repo_paths(tc: dict[str, str]) -> list[str]:
    """Every file path in the repo (vendored dirs included — callers filter)."""
    if _PATHS_KEY not in tc:
        raise MetaUnavailable("file list not fetched")
    return [p for p in tc[_PATHS_KEY].split("\n") if p]


def known_paths(tc: dict[str, str]) -> set[str]:
    """Every path we can name: scanned files plus, when it was fetched, the whole
    repo listing (so a file the text cache skipped — too big, `.mjs`, `.vue` — still counts)."""
    paths = {p.lstrip("/") for p in strip_meta(tc)}
    if _PATHS_KEY in tc:
        paths |= {p.lstrip("/") for p in repo_paths(tc)}
    return paths


def meta_file(tc: dict[str, str], name: str) -> str | None:
    """Content of a `META_FILES` entry, or None when the repo has no such file.

    Raises MetaUnavailable when the fetch never ran, so absent-file and
    never-looked stay distinguishable."""
    if _PATHS_KEY not in tc:
        raise MetaUnavailable("repo files not fetched")
    return tc.get(META_PREFIX + "file:" + name)


async def fetch_repo_meta(owner: str, repo: str, pat: str) -> tuple[list[str], dict[str, str]]:
    """File list + META_FILES contents.  Raises on a failed tree fetch; the
    caller decides whether the checkup can proceed without it."""
    from services.github_repo_fetch import _fetch_file, _list_repo_tree_with_sha
    from services.http import ext_client
    import httpx

    async with ext_client("github", timeout=httpx.Timeout(30.0, connect=6.0, read=15.0)) as client:
        blobs, _sha = await _list_repo_tree_with_sha(client, owner, repo, pat)
        paths = [b.get("path", "") for b in blobs]
        files: dict[str, str] = {}
        for name in META_FILES:
            if name in paths:
                text = await _fetch_file(client, owner, repo, name, pat)
                if text:
                    files[name] = text
    return paths, files


async def read_with_meta(owner: str, repo: str, pat: str, build_cache) -> dict[str, str]:
    """The text cache plus repo meta.  A meta failure degrades to the plain
    cache — meta-dependent checks then show as unverified, the rest still run."""
    tc = await build_cache(owner, repo, pat)
    try:
        paths, files = await fetch_repo_meta(owner, repo, pat)
    except Exception as e:                              # noqa: BLE001
        logger.warning("[checkup] repo meta unavailable %s/%s: %r", owner, repo, e)
        return tc
    return with_meta(tc, paths, files)
