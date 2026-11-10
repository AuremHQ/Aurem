"""
services/harness_content_repo.py — Aurem Loop harness, Phase A content.

Architecture decision (2026-11-10, founder-approved): the /harness
page's own copy lives in the separate, PRIVATE harness repo
(AuremHQ/aurem-loop, file web/harness-page-content.json) — not in
this app's own source tree. Reason: when the harness project later
gets its own subdomain (loop.auremcto.com), that exact file moves
with zero rewrite; ORA (this app) only ever fetches/serves it.

ORA's role is strictly READ at request time (routers/harness.py's
GET /harness/content) — the one WRITE path is the one-off/occasional
scripts/seed_harness_page_content.py, run manually, never from a
request handler.

Reuses the existing, already-vetted services.github_api_writer
(fetch_file / commit_files) instead of a second GitHub client.

Auth: AUREM_LOOP_REPO_TOKEN (backend .env) — a GitHub PAT scoped to
read (+write, for the seed script) the Contents API on that one
private repo. Never logged, never returned to any client.

Fails closed: a missing token, network error, or malformed JSON
serves the last successfully-fetched copy from the in-process cache;
with no cache yet, returns {"available": False} — the page shows
"temporarily unavailable", never stale fabricated copy, never a
crash.
"""
from __future__ import annotations

import json
import logging
import os
import time

from services.github_api_writer import commit_files, fetch_file

logger = logging.getLogger("aurem-dev.harness_content_repo")

HARNESS_REPO_OWNER = "AuremHQ"
HARNESS_REPO_NAME = "aurem-loop"
HARNESS_REPO_BRANCH = "main"
HARNESS_CONTENT_PATH = "web/harness-page-content.json"
_TOKEN_ENV = "AUREM_LOOP_REPO_TOKEN"
_CACHE_TTL_S = 300

# Module-level cache — one process, one copy. Multiple workers each
# keep their own, which is fine: worst case is a <=5min staleness
# skew across workers, never a crash, never fabricated content.
_cache: dict = {"data": None, "ts": 0.0}
# Negative cache for failed fetches (content missing / repo unreachable)
# so a 404 (e.g. content file not seeded yet) doesn't trigger a fresh
# outbound GitHub call on every single page load.
_fail_cache: dict = {"ts": 0.0}
_FAIL_CACHE_TTL_S = 60


_VALID_TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")


def _token() -> str | None:
    """Only ever returns a value that actually looks like a GitHub
    token. The repo is public as of 2026-11-10 (temporary, until a
    real PAT is added and it goes back to private), so a missing/
    malformed value here is NOT fatal for reads — fetch_file() is
    called with token=None, which GitHub's API treats as an
    unauthenticated (but still valid) public-repo read. It IS fatal
    for push_harness_page_content(), which raises explicitly rather
    than attempting an unauthenticated write (GitHub always rejects
    anonymous writes, public repo or not)."""
    raw = os.environ.get(_TOKEN_ENV) or ""
    if raw.startswith(_VALID_TOKEN_PREFIXES):
        return raw
    if raw:
        logger.warning(
            "%s is set but does not look like a GitHub token "
            "(got a non-token value, e.g. a URL) — treating as "
            "unauthenticated for reads; writes will fail closed.",
            _TOKEN_ENV,
        )
    return None


async def fetch_harness_page_content(force: bool = False) -> dict:
    """Returns the parsed content doc, with `available: True` set on
    success. On any failure, returns the last good cached copy (also
    `available: True`, just possibly stale up to _CACHE_TTL_S) or, if
    nothing has ever been fetched successfully, `{"available": False}`.
    Never raises.

    Reads proceed even with no token (token=None) — the repo is
    public as of 2026-11-10, so an unauthenticated GET still works;
    only the WRITE path (push_harness_page_content) hard-requires a
    token, since GitHub always rejects anonymous writes regardless of
    repo visibility."""
    now = time.time()
    if not force and _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL_S:
        return _cache["data"]
    if not force and _cache["data"] is None and (now - _fail_cache["ts"]) < _FAIL_CACHE_TTL_S:
        return {"available": False}

    token = _token()
    try:
        raw = await fetch_file(
            HARNESS_REPO_OWNER, HARNESS_REPO_NAME, HARNESS_CONTENT_PATH,
            ref=HARNESS_REPO_BRANCH, token=token,
        )
        if raw is None:
            raise ValueError(
                f"{HARNESS_CONTENT_PATH} not found in "
                f"{HARNESS_REPO_OWNER}/{HARNESS_REPO_NAME}@{HARNESS_REPO_BRANCH}",
            )
        parsed = json.loads(raw)
    except Exception as e:                                       # noqa: BLE001
        logger.warning("fetch_harness_page_content failed (serving cache): %r", e)
        _fail_cache["ts"] = now
        return _cache["data"] or {"available": False}

    parsed["available"] = True
    _cache["data"] = parsed
    _cache["ts"] = now
    return parsed


async def push_harness_page_content(content: dict, *, commit_message: str) -> dict:
    """WRITE path — only ever called from scripts/seed_harness_page_content.py,
    never from a request handler. Overwrites web/harness-page-content.json
    in the harness repo with `content` (pretty-printed, deterministic key
    order so diffs are reviewable)."""
    token = _token()
    if not token:
        raise RuntimeError(f"{_TOKEN_ENV} is not configured")
    body = json.dumps(content, indent=2, sort_keys=True) + "\n"
    result = await commit_files(
        HARNESS_REPO_OWNER, HARNESS_REPO_NAME, HARNESS_REPO_BRANCH, token,
        files={HARNESS_CONTENT_PATH: body},
        commit_message=commit_message,
        author_name="Aurem Loop Bot",
        author_email="aurem-user@users.noreply.github.com",
    )
    # Force the next read (by anyone, any process) to refetch rather
    # than serve a pre-seed cached copy for up to 5 minutes.
    _cache["data"] = None
    _cache["ts"] = 0.0
    return result
