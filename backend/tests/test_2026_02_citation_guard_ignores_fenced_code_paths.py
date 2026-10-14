"""
Regression for the reported bug: ORA investigates a flagged finding,
legitimately reads the real file via `read_repo_file`, quotes its
source back to the user — and the quoted source happens to contain a
relative import (`import('../../refresh-skills-plugin.js')`). The old
`_FILE_PATH` regex matched that import specifier INSIDE the quoted
code fence as if it were a second, independent citation claim. Since
it was never itself passed to `read_repo_file`, the guard tried to
re-fetch it literally as a repo-root path, got a miss, and told the
LLM to say "FILE NOT FOUND: ../../refresh-skills-plugin.js" — making a
fully grounded investigation look broken/hallucinatory.

Fix: `_extract_claims` now strips fenced code blocks before running
the path/version/count regexes, so quoted file CONTENT is never
treated as a separate citation claim. Prose claims around a fence
(the actual "I read `X`" statement) must still be verified exactly as
before — this test asserts both halves.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.citation_guard import CitationGuard, _extract_claims


def test_real_world_relative_import_inside_quoted_code_is_not_a_claim():
    """Exact shape of the reported incident: a genuinely-read file is
    quoted in a fence containing a relative import path."""
    text = (
        "On `.agent/skills/apps/web-app/src/__tests__/"
        "refresh-skills-plugin.security.test.js` (flagged for "
        "`token_assignment`) — I read the file; here's the relevant "
        "content so we can figure out together whether this is a "
        "real issue:\n"
        "```\n"
        "async function loadRefreshHandler() {\n"
        "  const { default: refreshSkillsPlugin } = "
        "await import('../../refresh-skills-plugin.js');\n"
        "}\n"
        "```"
    )
    tool_calls = [{
        "tool": "read_repo_file",
        "args": {"path": ".agent/skills/apps/web-app/src/__tests__/"
                         "refresh-skills-plugin.security.test.js"},
    }]
    g = CitationGuard()
    report = g.verify(text, tool_calls)
    assert "../../refresh-skills-plugin.js" not in report["claims"]["paths"], (
        "quoted import specifier inside the code fence must not be "
        "extracted as a standalone citation claim"
    )
    assert report["pass"] is True, (
        f"guard should pass — the real cited file WAS read; "
        f"unverified_paths={report['unverified_paths']}"
    )


def test_prose_citation_outside_a_fence_still_requires_verification():
    """The actual bug-fix must not weaken the guard's real job: a path
    stated in PROSE (not quoted code) with no matching tool call is
    still a hallucination risk and must still be blocked."""
    text = "I checked `backend/server.py` and it looks fine."
    report = CitationGuard().verify(text, tool_calls=[])
    assert report["pass"] is False
    assert "backend/server.py" in report["unverified_paths"]


def test_extract_claims_strips_fenced_blocks_entirely():
    text = (
        "See `real/file.py` for details.\n"
        "```python\n"
        "from fake/not_a_real_path.py import thing\n"
        "```"
    )
    claims = _extract_claims(text)
    assert claims["paths"] == ["real/file.py"]
