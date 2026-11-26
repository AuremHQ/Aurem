"""
test_part7_knowledge_runner.py — Part VII audit fix 2.

Offline tests for the knowledge-docs approval gate in
backend/evals/runner.py (_knowledge_approval_test + its wiring into
run()). No LLM, no Mongo, no backend boot.

Run from backend/ with the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part7_knowledge_runner.py -q

Rule pinned here: the loader (services/knowledge_injector.py) injects
any file whose front-matter says `status: approved` and NEVER reads
approved_by/approved_on. So approved + blank approval fields must
HARD-fail; drafts pass.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import evals.runner as runner
from evals.harness import ScoreResult, FAIL, PASS


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _doc(status="draft", by=None, on=None, extra=""):
    by_s = "null" if by is None else by
    on_s = "null" if on is None else on
    return (
        f"---\nstatus: {status}\napproved_by: {by_s}\n"
        f"approved_on: {on_s}\n{extra}---\n\nBody text.\n"
    )


def _knowledge_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    base = tmp_path / "knowledge"
    for name, text in files.items():
        _write(base / name, text)
    return base


def _run_check(base: Path) -> dict:
    return asyncio.run(runner._knowledge_approval_test(knowledge_dir=base))


def _by_file(result: dict, name: str) -> dict:
    for s in result["scorers"]:
        if s["details"].get("file") == name:
            return s
    raise AssertionError(f"no scorer for {name} in {result['scorers']}")


def test_drafts_pass(tmp_path):
    base = _knowledge_dir(tmp_path, {
        "product.md": _doc(),
        "pricing.md": _doc(),
        "common-fixes.md": _doc(),
        "security-checks.md": _doc(),
        "examples/example_01_null_check_fix.md": _doc(),
    })
    res = _run_check(base)
    assert res["files_checked"] == 5
    assert res["hard_fails"] == 0
    assert {s["status"] for s in res["scorers"]} == {PASS}


def test_approved_with_blank_fields_hard_fails(tmp_path):
    """The exact gap: approved + blank approved_by/approved_on."""
    base = _knowledge_dir(tmp_path, {
        "product.md": _doc(status="approved"),
        "pricing.md": _doc(),
        "common-fixes.md": _doc(),
        "security-checks.md": _doc(),
    })
    res = _run_check(base)
    assert res["hard_fails"] == 1
    s = _by_file(res, "product.md")
    assert s["status"] == FAIL and s["severity"] == "hard"
    assert "approved_by" in s["evidence"] and "approved_on" in s["evidence"]
    assert _by_file(res, "pricing.md")["status"] == PASS


def test_approved_with_evidence_passes(tmp_path):
    base = _knowledge_dir(tmp_path, {
        "product.md": _doc(status="approved", by="teji", on="2026-11-25"),
        "pricing.md": _doc(status="approved", by="teji",
                           on="2026-11-25T10:00:00"),
        "common-fixes.md": _doc(),
        "security-checks.md": _doc(),
    })
    res = _run_check(base)
    assert res["hard_fails"] == 0
    assert _by_file(res, "product.md")["status"] == PASS
    # approved_on as a YAML datetime also parses to a real date.
    assert _by_file(res, "pricing.md")["status"] == PASS


def test_approved_with_invalid_dates_fail(tmp_path):
    cases = {
        "product.md": _doc(status="approved", by="teji", on="25 Nov 2026"),
        "pricing.md": _doc(status="approved", by="teji", on="2026-02-30"),
        "common-fixes.md": _doc(status="approved", by="", on="2026-11-25"),
        "security-checks.md": _doc(status="approved", by="teji", on=""),
    }
    base = _knowledge_dir(tmp_path, cases)
    res = _run_check(base)
    assert res["hard_fails"] == 4
    for name in cases:
        assert _by_file(res, name)["status"] == FAIL


def test_missing_frontmatter_and_unknown_status_fail(tmp_path):
    base = _knowledge_dir(tmp_path, {
        "product.md": "No front-matter here.\n",
        "pricing.md": _doc(status="pending"),
        "common-fixes.md": _doc(),
        "security-checks.md": _doc(),
    })
    res = _run_check(base)
    assert res["hard_fails"] == 2
    assert _by_file(res, "product.md")["status"] == FAIL
    assert _by_file(res, "pricing.md")["status"] == FAIL


def test_missing_candidate_file_fails(tmp_path):
    base = _knowledge_dir(tmp_path, {
        "product.md": _doc(),
        "pricing.md": _doc(),
        "common-fixes.md": _doc(),
        # security-checks.md absent on purpose
    })
    res = _run_check(base)
    assert res["hard_fails"] == 1
    assert _by_file(res, "security-checks.md")["status"] == FAIL


def test_examples_are_covered_but_marketing_style_is_not(tmp_path):
    """Examples share the loader's status-only gate, so they are
    checked; marketing-style.md is excluded by the loader by name,
    so an (invalid) approved marketing-style must NOT fail the gate."""
    base = _knowledge_dir(tmp_path, {
        "product.md": _doc(),
        "pricing.md": _doc(),
        "common-fixes.md": _doc(),
        "security-checks.md": _doc(),
        "examples/example_01_null_check_fix.md": _doc(status="approved"),
        "marketing-style.md": _doc(status="approved"),
    })
    res = _run_check(base)
    assert res["hard_fails"] == 1
    assert _by_file(res, "example_01_null_check_fix.md")["status"] == FAIL
    assert all(s["details"].get("file") != "marketing-style.md"
               for s in res["scorers"])


def test_real_tree_knowledge_dir_all_drafts():
    """The seeded tree's own knowledge dir: 4 candidates + 10 examples,
    all draft — the gate passes on the real repo state."""
    res = asyncio.run(runner._knowledge_approval_test())
    assert res["files_checked"] == 14
    assert res["hard_fails"] == 0


# ── Wiring into run(): before the no-LLM-key early return ───────────

def _run_keyless(monkeypatch):
    for var in ("EMERGENT_LLM_KEY", "OPENROUTER_API_KEY",
                "MONGO_URL", "DB_NAME"):
        monkeypatch.delenv(var, raising=False)
    return asyncio.run(runner.run(quick=True))


def test_run_keyless_still_runs_knowledge_gate(monkeypatch):
    """Keyless CI used to return ok=True before any offline test ran.
    The knowledge gate now runs first and its verdict decides ok."""
    rep = _run_keyless(monkeypatch)
    assert rep["knowledge_approval"] is not None
    assert rep["knowledge_approval"]["files_checked"] == 14
    assert rep["knowledge_approval"]["hard_fails"] == 0
    assert rep["ok"] is True
    assert "skipped" in rep


def test_run_keyless_blocked_when_gate_fails(monkeypatch):
    async def _failing(knowledge_dir=None):
        return {
            "id": "K1_knowledge_approval",
            "category": "knowledge_approval",
            "prompt": "(offline) test stub",
            "scorers": [ScoreResult(
                scorer="knowledge_approval", status=FAIL, severity="hard",
                evidence="product.md: approved but approved_by is blank",
                details={"file": "product.md"},
            ).as_dict()],
            "hard_fails": 1,
            "soft_fails": 0,
            "files_checked": 1,
        }

    monkeypatch.setattr(runner, "_knowledge_approval_test", _failing)
    rep = _run_keyless(monkeypatch)
    assert rep["ok"] is False  # deploy-blocking even with no LLM key
    # The printed report must agree with the exit code (pre-existing
    # display quirk on the keyless path: it used to print PASS while
    # main() exited 1 once ok could be False there).
    assert "❌ BLOCKED" in runner._format_report(rep)


def test_format_report_passes_when_gate_passes(monkeypatch):
    rep = _run_keyless(monkeypatch)
    assert "✅ PASS" in runner._format_report(rep)
