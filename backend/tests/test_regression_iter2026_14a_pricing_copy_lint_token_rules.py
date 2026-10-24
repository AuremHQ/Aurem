"""tests/test_regression_iter2026_14a_pricing_copy_lint_token_rules.py

Master Prompt v2 PART 5.1/5.2 — extends scripts/pricing_copy_lint.py
(the existing Iter 364 "unlimited near pricing keyword" CI gate) with
two NEW rules, without weakening or removing the original one:

  1. ALWAYS-ON: bans per-action token-cost phrasing ("tokens per fix",
     "tokens per task", "costs N tokens", "N tokens per") in any
     scanned marketing/app file — the Part 4.3 display rule applies
     to copy, not just to live component state.
  2. MODE-AWARE cutover forcing-function: when TOKEN_BILLING_MODE is
     NOT "off", the OLD task-based plan-wording phrases (the Part 5.0
     inventory's SWITCH-GATED rows: "N tasks/month", "N free tasks",
     "no token billing"/"no per-token" claims) become forbidden in
     the same scanned files. While TOKEN_BILLING_MODE stays "off"
     (this delivery), these phrases are still ALLOWED (they're
     accurate today) — so the lint stays green now and goes red
     automatically the moment a real deploy flips the env var,
     forcing a human to update the static/SEO marketing files this
     turn does not rewrite (see PART 5 report for the full list).

This file is NEW test coverage for scripts/pricing_copy_lint.py,
which had none before (only exercised directly by CI, never asserted
against in a test). The PRE-EXISTING "unlimited near pricing keyword"
behavior is re-verified here too so a future edit can't silently
weaken it.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def lint_module(monkeypatch, tmp_path):
    """Re-import the module fresh per test so TOKEN_BILLING_MODE env
    changes are picked up (the module reads it at call time, not
    import time, but we reload defensively in case that changes)."""
    import scripts.pricing_copy_lint as mod
    importlib.reload(mod)
    return mod


def _scan_text(lint_module, text: str, *, mode: str | None = None, env: dict | None = None):
    """Run the per-action-cost + mode-aware checks against a raw
    string (no real file needed) by calling the module's own
    line-scanning helpers directly."""
    if mode is not None:
        os.environ["TOKEN_BILLING_MODE"] = mode
    elif "TOKEN_BILLING_MODE" in os.environ:
        del os.environ["TOKEN_BILLING_MODE"]
    hits = []
    hits.extend(lint_module._scan_per_action_cost(text))
    hits.extend(lint_module._scan_legacy_task_wording(text, mode=lint_module._current_mode()))
    return hits


class TestPreExistingUnlimitedRuleStillWorks:
    def test_unlimited_near_pricing_keyword_still_flagged(self, lint_module, tmp_path):
        f = tmp_path / "Foo.jsx"
        f.write_text("Pro tier gives you unlimited tasks every month.")
        hits = lint_module._scan_file(f, set())
        assert hits, "the original Iter 364 drift guard must still fire"

    def test_is_unlimited_identifier_still_exempt(self, lint_module, tmp_path):
        f = tmp_path / "Foo.jsx"
        f.write_text("if (user.is_unlimited) { showBadge(); }")
        hits = lint_module._scan_file(f, set())
        assert hits == []


class TestPerActionCostBanIsAlwaysOn:
    @pytest.mark.parametrize("phrase", [
        "This fix costs 4 tokens.",
        "Each chat turn is 2 tokens per message.",
        "Tokens per fix: 4",
        "4 tokens per task on average.",
    ])
    def test_per_action_cost_phrasing_banned_regardless_of_mode(self, lint_module, phrase):
        hits_off = _scan_text(lint_module, phrase, mode="off")
        hits_on = _scan_text(lint_module, phrase, mode="on")
        assert hits_off, f"should flag even with mode=off: {phrase!r}"
        assert hits_on, f"should flag with mode=on: {phrase!r}"

    def test_aggregate_balance_copy_is_not_banned(self, lint_module):
        hits = _scan_text(lint_module, "You have 820 tokens left this month.", mode="on")
        assert hits == []


class TestModeAwareLegacyWordingForcingFunction:
    @pytest.mark.parametrize("phrase", [
        "Start free — 10 tasks",
        "10 free tasks, no credit card required.",
        "Claim 10 free tasks",
        "50 tasks per month",
        "10 tasks/month",
        "no token billing",
        "no per-token pricing",
        "Pricing is flat, not per-token",
    ])
    def test_legacy_task_wording_allowed_while_mode_off(self, lint_module, phrase):
        hits = _scan_text(lint_module, phrase, mode="off")
        assert hits == [], f"legacy wording must stay allowed while mode=off: {phrase!r}"

    @pytest.mark.parametrize("phrase", [
        "Start free — 10 tasks",
        "10 free tasks, no credit card required.",
        "Claim 10 free tasks",
        "50 tasks per month",
        "10 tasks/month",
        "no token billing",
        "no per-token pricing",
        "Pricing is flat, not per-token",
    ])
    def test_legacy_task_wording_forbidden_once_mode_is_on(self, lint_module, phrase):
        hits = _scan_text(lint_module, phrase, mode="on")
        assert hits, f"legacy wording must be flagged once mode=on (cutover forcing function): {phrase!r}"

    @pytest.mark.parametrize("phrase", [
        "Iter 73 Task 1 — initial live tape.",
        "2026-08-28 · NEW P0 Task 1d — the ship-cta-fallback banner",
        "0 tasks run — candidates for re-engagement",
        "10/25/50 task auto-prompt -> /wrapped (Iter 77)",
        "Fix · 1 task",
        "task-quota gating (1 fix = 1 task, tier-gated)",
    ])
    def test_unrelated_task_mentions_are_not_flagged_as_billing_copy(self, lint_module, phrase):
        """Ticket/iteration numbering, admin-analytics counts, and
        the generic 1-fix-=-1-task UNIT labels are NOT billing-plan
        claims — flagging them would be noisy enough that nobody
        would trust (or fix) real hits once the lint goes live."""
        hits = _scan_text(lint_module, phrase, mode="on")
        assert hits == [], f"false positive on a non-billing 'task' mention: {phrase!r}"

    def test_legacy_task_wording_forbidden_in_shadow_mode_too(self, lint_module):
        hits = _scan_text(lint_module, "10 free tasks", mode="shadow")
        assert hits

    def test_unset_env_defaults_to_off_and_allows_legacy_wording(self, lint_module):
        hits = _scan_text(lint_module, "10 free tasks", mode=None)
        assert hits == []

    def test_unrecognized_mode_value_fails_safe_to_off(self, lint_module):
        """Mirrors services.wallet.billing_mode()'s own fail-safe: a
        typo'd env value must never be treated as "on"."""
        hits = _scan_text(lint_module, "10 free tasks", mode="totally-bogus")
        assert hits == []


class TestTestFilesAreNeverScanned:
    """Rules 4/5 run against the real repo tree in main(), not just
    raw strings — test files legitimately reference BOTH legacy and
    token wording as string literals (to assert a transition), which
    would otherwise flood the lint with false positives the moment
    any test (including this very file) exists."""

    def test_dunder_tests_dir_is_excluded_from_main_scan(self, lint_module, tmp_path, monkeypatch):
        fe = tmp_path / "frontend" / "src" / "__tests__"
        fe.mkdir(parents=True)
        (fe / "Foo.test.jsx").write_text('expect(x).toBe("10 free tasks");')
        pub = tmp_path / "frontend" / "public"
        pub.mkdir(parents=True)

        monkeypatch.setattr(lint_module, "ROOT", tmp_path)
        monkeypatch.setattr(lint_module, "FRONTEND", tmp_path / "frontend" / "src")
        monkeypatch.setattr(lint_module, "PUBLIC", pub)
        monkeypatch.setattr(lint_module, "SCAN_DIRS", [tmp_path / "frontend" / "src", pub])
        monkeypatch.setattr(lint_module, "ALLOWLIST_FILE", tmp_path / "allowlist.txt")
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")

        exit_code = lint_module.main()
        assert exit_code == 0

    def test_test_suffixed_file_outside_a_tests_dir_is_also_excluded(self, lint_module, tmp_path, monkeypatch):
        fe = tmp_path / "frontend" / "src"
        fe.mkdir(parents=True)
        (fe / "Foo.test.jsx").write_text('expect(x).toBe("10 free tasks");')
        pub = tmp_path / "frontend" / "public"
        pub.mkdir(parents=True)

        monkeypatch.setattr(lint_module, "ROOT", tmp_path)
        monkeypatch.setattr(lint_module, "SCAN_DIRS", [fe, pub])
        monkeypatch.setattr(lint_module, "ALLOWLIST_FILE", tmp_path / "allowlist.txt")
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")

        exit_code = lint_module.main()
        assert exit_code == 0


class TestEmptyAndEdgeCaseInputs:
    def test_empty_string(self, lint_module):
        assert _scan_text(lint_module, "", mode="on") == []

    def test_unicode_and_emoji_do_not_crash(self, lint_module):
        hits = _scan_text(lint_module, "🚀 10 free tasks — 日本語テスト", mode="on")
        assert hits  # still catches "10 free tasks" even with surrounding unicode

    def test_huge_input_does_not_crash_or_hang(self, lint_module):
        text = ("filler line, nothing to see here.\n" * 50_000) + "10 free tasks\n"
        hits = _scan_text(lint_module, text, mode="on")
        assert hits
