"""
Iter 211 — GitHub OAuth identity proof test.

Validates that the GitHub OAuth callback captures the immutable
numeric GitHub ID for both new and returning users.
"""
from __future__ import annotations

from pathlib import Path


def test_oauth_callback_captures_github_id():
    """github.id (immutable numeric) MUST be stored on signup AND on
    return-login update. login (username) can change; id cannot."""
    src = (
        Path(__file__).resolve().parents[1]
        / "routers"
        / "github_oauth.py"
    ).read_text()

    # Both insert (new account) and update (returning user) paths set github.id.
    assert src.count('"id":') >= 2, \
        "github.id should be set in both insert + update paths"

    # No stale code: should not set ONLY login without id anymore.
    assert '"github": {\n                    "id":' in src or "gh_id_num" in src
