"""
services/github_errors.py — shared GitHub/infra-failure -> HTTP status
mapper for the "Save to GitHub" modal's read endpoints
(routers/cto_projects/github_browse.py) and submit-time repo-list
check (routers/cto_projects/tasks.py::submit_task).

Root-cause context (2026-10-27 diagnosis): all three call sites used
`raise HTTPException(502, ...)` when `list_installation_repos()` (or
`list_repo_branches()`) failed. Cloudflare — and most edge/CDN layers —
REPLACE the response BODY of a 502/503/504 with their own HTML/text
before it reaches the browser; the honest `classify_error(e)
["user_message"]` string never made it to the user, who saw raw
Cloudflare infra text instead ("the origin web server returned an
invalid or incomplete response"). `routers/cto_projects/management.py`
already hit and fixed this exact edge behavior for the `/simplify`
endpoints (its 2026-02 comment) by switching to 424 — this module
generalizes that same fix for the three Save-to-GitHub call sites.

`classify_github_failure()` returns a structured, English, token/URL-
free detail dict; `GITHUB_FAILURE_HTTP_STATUS` (424) is the one status
every caller here should raise with it — never 502/503/504.
"""
from __future__ import annotations

import httpx

from services.retry_guard import BreakerOpenError

# 424 Failed Dependency — a real 4xx, so edge/CDN layers pass the body
# through untouched (never replaced the way 502/503/504 bodies are).
GITHUB_FAILURE_HTTP_STATUS = 424


def classify_github_failure(exc: Exception) -> dict:
    """Map any exception raised while talking to GitHub into a safe,
    user-facing `{code, message, retryable}` triple.

    NEVER includes the raw exception string, a token, or a URL in the
    returned `message` — those belong in the logger call the caller
    already makes (`logger.warning(..., exc)`), never the response
    body a browser renders."""
    if isinstance(exc, BreakerOpenError):
        return {
            "code": "github_unavailable",
            "message": "GitHub looks unreachable right now — please try again in a moment.",
            "retryable": True,
        }
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return {
                "code": "github_auth_rejected",
                "message": ("GitHub rejected the request — the App installation may be "
                            "suspended or revoked. Reconnect via Projects → APP."),
                "retryable": False,
            }
        if status == 404:
            return {
                "code": "github_not_found",
                "message": "GitHub couldn't find that repository or installation.",
                "retryable": False,
            }
        if status == 429:
            return {
                "code": "github_rate_limited",
                "message": "GitHub rate-limited this request — please try again shortly.",
                "retryable": True,
            }
        if status >= 500:
            return {
                "code": "github_upstream_error",
                "message": "GitHub is having issues right now — please try again in a moment.",
                "retryable": True,
            }
        return {
            "code": "github_rejected",
            "message": "GitHub rejected the request.",
            "retryable": False,
        }
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return {
            "code": "github_timeout",
            "message": "Couldn't reach GitHub in time — please try again.",
            "retryable": True,
        }
    return {
        "code": "github_error",
        "message": "Something went wrong talking to GitHub — please try again.",
        "retryable": True,
    }


__all__ = ["GITHUB_FAILURE_HTTP_STATUS", "classify_github_failure"]
