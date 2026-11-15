"""tests/test_regression_iter2026_31_brain_part4_f6_eval_score.py

ORA Brain Part 4, F6 — the offline eval (scripts/ora_brain_offline_
eval.py) was 7/10 before this fix: 3 natural phrasings ("Swift/Pro
mode", "XSS mitigation", "direct push to branch") didn't match any
`inject_on` keyword list in backend/knowledge/*.md. Fixed the
keyword lists (files stay `status: draft` — never auto-approved) and
extended the sample set to 16 phrasings. This test runs the SAME
eval harness the script uses and asserts 16/16.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "ora_brain_offline_eval.py"


def _load_eval_module():
    spec = importlib.util.spec_from_file_location("ora_brain_offline_eval", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestOfflineEvalScore:
    async def test_sample_set_has_at_least_15_phrasings(self):
        mod = _load_eval_module()
        assert len(mod.SAMPLE_TASKS) >= 15

    async def test_full_eval_scores_100_percent(self):
        mod = _load_eval_module()
        mod._force_all_approved_in_memory()
        failures = 0
        for task, expected_file in mod.SAMPLE_TASKS:
            off_ok, selected_ok, unknown_rule_ok = await mod._run_one(task, expected_file)
            if not (off_ok and selected_ok and unknown_rule_ok):
                failures += 1
        os.environ.pop("ORA_KNOWLEDGE_MODE", None)
        assert failures == 0, f"{failures}/{len(mod.SAMPLE_TASKS)} sample tasks failed"

    async def test_knowledge_files_still_all_draft(self):
        """F6 explicit constraint: fixing keyword coverage must never
        flip a file to approved."""
        import re
        knowledge_dir = Path(__file__).resolve().parent.parent / "knowledge"
        for md_file in knowledge_dir.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            m = re.search(r"^status:\s*(\w+)", text, re.MULTILINE)
            assert m and m.group(1) == "draft", f"{md_file.name} is not status: draft"

    async def test_the_three_originally_failing_phrasings_now_pass(self):
        mod = _load_eval_module()
        mod._force_all_approved_in_memory()
        originally_failing = [
            ("Explain how Swift mode differs from Pro mode", "product.md"),
            ("What's our XSS mitigation pattern for repo-derived strings?", "common-fixes.md"),
            ("Does ORA ship a direct push to my branch?", "product.md"),
        ]
        for task, expected_file in originally_failing:
            _off_ok, selected_ok, _unknown_rule_ok = await mod._run_one(task, expected_file)
            assert selected_ok, f"still failing: {task!r}"
        os.environ.pop("ORA_KNOWLEDGE_MODE", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
