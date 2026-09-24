"""
services/health_reporter.py — 2026-09-21 (decision-point audit,
report-identity fix)

Root cause of the mis-attributed-report incident this module follows
up on: it was NOT a code bug. All 4 audited decision points (health
gate, coverage-scan classification, notifier routing, repo-identity
path-scoping) were already correct and deterministic — see
tests/unit/. The actual incident was a HUMAN reading a pasted health/
complexity report with no identity header and assuming it described
AUREM's own code, when it actually described a different connected
repo.

Every renderer of a health/complexity report (services/health_score.py
`get_health_score()`, routers/codebase_health.py `scan()`, scripts/
self_scan.py) puts this header at the TOP of what it returns/writes,
so which repo a report describes is never ambiguous again — even once
pasted somewhere else, stripped of any surrounding context.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_identity_header(
    owner: str, repo: str, branch: str,
    head_sha: Optional[str], scan_kind: str,
) -> str:
    """Locked format:
    `Repo: {owner}/{repo}@{branch} | SHA: {head_sha[:8]} | Scan: customer|self | Generated: {utc_now_iso()}`
    """
    if scan_kind not in ("customer", "self"):
        raise ValueError("scan_kind must be 'customer' or 'self'")
    sha_display = (head_sha or "unknown")[:8]
    return (f"Repo: {owner}/{repo}@{branch} | SHA: {sha_display} | "
            f"Scan: {scan_kind} | Generated: {utc_now_iso()}")


def self_repo_identity(repo_root: str = "/app") -> tuple[str, str, str, Optional[str]]:
    """AUREM's own (owner, repo, branch, head_sha) — read straight from
    git, so a self-scan report is self-describing without needing any
    DB lookup. Best-effort: never raises; missing git info degrades to
    "unknown" rather than blocking a report from rendering."""
    def _git(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(
                ["git", "-C", repo_root, *args],
                capture_output=True, text=True, timeout=5,
            )
            return out.stdout.strip() or None if out.returncode == 0 else None
        except Exception:  # noqa: BLE001
            return None

    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    head_sha = _git("rev-parse", "HEAD")
    remote_url = _git("remote", "get-url", "origin") or ""
    owner, repo = "unknown", "unknown"
    # Normalize both "https://github.com/owner/repo.git" and
    # "git@github.com:owner/repo.git" (scp-form) to the same
    # slash-separated shape, then take the last two segments.
    tail = remote_url.rstrip("/").removesuffix(".git").replace(":", "/")
    segments = [s for s in tail.split("/") if s]
    if len(segments) >= 2:
        owner, repo = segments[-2], segments[-1]
    return owner, repo, branch, head_sha


__all__ = ["format_identity_header", "utc_now_iso", "self_repo_identity"]
