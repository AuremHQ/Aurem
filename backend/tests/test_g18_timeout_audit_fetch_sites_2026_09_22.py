"""
tests/test_g18_timeout_audit_fetch_sites_2026_09_22.py

Regression lock for G18 · Timeout Audit (was RED: "4 unbounded I/O
sites detected"). The 4 sites were frontend fetch() calls with no
abort/timeout signal:
  - frontend/src/components/VisualDiffWorkbench.jsx (authedFetch wrapper)
  - frontend/src/components/ChatPanel.jsx  (upload / analyze / discard)

Fix added `signal: AbortSignal.timeout(ms)` to each (none are SSE
streams, so a hard timeout is correct). This uses the real static
auditor `scripts.timeout_audit.run_audit()` — the exact check the guard
runs — so it FAILS pre-fix (4 violations) and PASSES after (0).
"""
from scripts.timeout_audit import run_audit

_PREVIOUSLY_FLAGGED = {
    "frontend/src/components/VisualDiffWorkbench.jsx",
    "frontend/src/components/ChatPanel.jsx",
}


def test_g18_audit_has_no_unbounded_io_sites():
    r = run_audit()
    assert r["pass"] is True, f"still {len(r['violations'])} unbounded I/O sites: {r['violations']}"
    assert r["violations"] == []


def test_previously_flagged_fetch_sites_now_covered():
    violations = run_audit()["violations"]
    offenders = {v["file"] for v in violations}
    leaked = _PREVIOUSLY_FLAGGED & offenders
    assert not leaked, f"these files still have unbounded fetch() calls: {leaked}"
