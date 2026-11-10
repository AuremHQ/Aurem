"""tests/test_regression_iter2026_65_vanguard_destructive_file_ops.py

Devil's-advocate audit finding (Phase 1 — LLM-diff safety backstop):
services/vanguard_scanner.py's dangerous-pattern list already blocks
eval/exec/subprocess(shell=True)/os.system/pickle.loads/
yaml.load(unsafe)/sql-string-format/innerHTML/dangerouslySetInnerHTML
— real, code-level, prompt-independent gates on every
write_repo_file() call (confirmed: write_repo_file blocks on
CRITICAL+HIGH Vanguard findings before ever reaching commit_files()).

Gap found: NOTHING in that list catches `shutil.rmtree(...)`,
`os.remove(...)`, or `os.unlink(...)` — the same "destructive,
shell-adjacent capability written into shippable code" risk class as
`os.system` (already HIGH), just conspicuously absent. A patch
containing one of these currently sails through Vanguard with zero
findings and reaches the user's repo.

This file proves the gap (RED), then (after the scanner fix) proves
it's closed (GREEN) at both layers: the scanner's own pattern match,
and the real end-to-end write_repo_file() block (same style as
test_iter212m6_tool_reliability_full.py's
test_write_repo_file_blocks_critical_secret).
"""
from __future__ import annotations

import pytest

from services.local_tools import write_repo_file
from services.vanguard_scanner import DANGEROUS_PATTERNS, scan_text

SHUTIL_RMTREE = 'import shutil\nshutil.rmtree(user_supplied_path)'
OS_REMOVE = 'import os\nos.remove(target_file)'
OS_UNLINK = 'import os\nos.unlink(target_file)'


class TestVanguardCatchesDestructiveFileOps:
    def test_rule_names_are_registered(self):
        names = {name for name, _pattern, _sev in DANGEROUS_PATTERNS}
        assert "shutil_rmtree" in names, "shutil.rmtree has no Vanguard rule"
        assert "os_remove" in names, "os.remove has no Vanguard rule"
        assert "os_unlink" in names, "os.unlink has no Vanguard rule"

    @pytest.mark.parametrize("payload,expected_rule", [
        (SHUTIL_RMTREE, "shutil_rmtree"),
        (OS_REMOVE, "os_remove"),
        (OS_UNLINK, "os_unlink"),
    ])
    def test_scan_text_flags_each_pattern(self, payload, expected_rule):
        findings = scan_text(payload, filepath="api/cleanup.py")
        names = {f["name"] for f in findings}
        assert expected_rule in names, (
            f"scan_text found {names!r}, expected {expected_rule!r} in there"
        )
        hit = next(f for f in findings if f["name"] == expected_rule)
        # Same severity convention as the existing os_system rule —
        # not inventing a new tier, matching the adjacent precedent.
        assert hit["severity"] == "HIGH"

    def test_a_clean_file_using_the_word_remove_in_a_comment_is_not_flagged(self):
        """Sanity check against over-matching — a docstring or comment
        that merely mentions removal must not trip the rule; only a
        real call expression should."""
        findings = scan_text(
            "# TODO: remove this function later\ndef noop():\n    pass\n",
            filepath="api/clean.py",
        )
        names = {f["name"] for f in findings}
        assert "os_remove" not in names
        assert "os_unlink" not in names
        assert "shutil_rmtree" not in names


class TestWriteRepoFileBlocksDestructiveFileOps:
    pytestmark = pytest.mark.asyncio

    @pytest.mark.parametrize("payload,expected_rule", [
        (SHUTIL_RMTREE, "shutil_rmtree"),
        (OS_REMOVE, "os_remove"),
        (OS_UNLINK, "os_unlink"),
    ])
    async def test_write_repo_file_blocks_before_commit(self, monkeypatch, payload, expected_rule):
        # Current (post bin_ctx-refactor) fixture shape — write_repo_file
        # reads owner/repo/branch/token from ctx["bin_ctx"] via
        # _repo_ctx_from(), not the legacy _resolve_project() DB lookup
        # (that path is quarantined/stale — confirmed via
        # legacy_quarantine.txt, not used here). Same _FakeBinCtx shape
        # as tests/test_phase2c_local_tools_wave2.py.
        class _FakeBinCtx:
            def __init__(self):
                self.bin_id = "u1"
                self.repo_owner = "alice"
                self.repo_name = "myrepo"
                self.branch = "main"
                self.pat = "ghp_xxxxx"
                self.is_founder = False
                self.pid = "p1"

        ctx = {"bin_ctx": _FakeBinCtx(), "user_id": "u1"}

        commit_called = {"yes": False}

        async def _spy_commit(**kwargs):
            commit_called["yes"] = True
            return {"ok": True}
        monkeypatch.setattr("services.github_api_writer.commit_files", _spy_commit)

        res = await write_repo_file(ctx, {"path": "cleanup.py", "content": payload})
        assert res["ok"] is False
        assert "vanguard" in res["error"].lower()
        assert commit_called["yes"] is False, (
            f"commit_files() was called despite a {expected_rule} payload — "
            "the gate did not block before the write reached the repo"
        )
        assert any(f["rule"] == expected_rule for f in res.get("findings", []))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
