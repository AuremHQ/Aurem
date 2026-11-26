"""Prompt 3 item 2 (2026-11-26) — category-dispatched re-validation.

Covers services.finding_fix_applier._finding_still_present() AFTER the
Vanguard-only body was replaced by category dispatch:

  (a) a security-category finding is still re-validated via Vanguard
      (still-present detected, fixed detected);
  (b) a non-security category is dispatched to its own checker — the
      REAL performance + dependencies scanners (pure functions, no
      network) and a STUBBED checker proving the routing itself;
  (c) an unknown/missing category falls back to Vanguard;
  (d) a finding the old Vanguard-only code would have caught is still
      caught (no weakening) — including through the Vanguard backstop
      when the category routing points elsewhere.

No network, no real LLMs, no DB anywhere in this file: the real
checkers exercised here (Vanguard scan_text, the SCANNERS entries)
are pure in-process functions, and the stubbed cases use monkeypatch.
"""
from __future__ import annotations

import pytest

import services.codebase_health_core as core
import services.finding_fix_applier as ffa

# Real Vanguard-detectable fixtures (verified against scan_text at
# 190930d2 — the sk-proj string fires "generic_api_key", not
# "secret_openai_key"; `eval(` fires "eval_usage").
_SECRET_CONTENT = 'openai.api_key = "sk-proj-abcdefghij1234567890"\n'
_CLEAN_CONTENT = 'openai.api_key = os.environ.get("OPENAI_API_KEY")\n'
_EVAL_CONTENT = "x = eval(user_input)\n"

# Real performance-scanner fixtures (verified against
# SCANNERS["performance"] — `.to_list(None)` fires "unbounded_tolist",
# `.to_list(50)` fires nothing).
_PERF_STILL = "rows = await db.items.find().to_list(None)\n"
_PERF_FIXED = "rows = await db.items.find().to_list(50)\n"

# Real dependencies-scanner fixtures (verified against
# SCANNERS["dependencies"] with a cold _OSV_CACHE — the static _CVE_DB
# maps requests 2.28.* to CVE-2023-32681).
_DEP_STILL = "requests==2.28.0\n"
_DEP_FIXED = "requests==2.32.3\n"


def _sec_finding(rule_id="generic_api_key", **extra):
    return {"rule_id": rule_id, "file": "app.py", "line": 1, **extra}


# ─── (a) security via Vanguard ────────────────────────────────────────
class TestSecurityStillUsesVanguard:
    def test_secret_still_present_detected(self):
        assert ffa._finding_still_present(
            _SECRET_CONTENT, "app.py", _sec_finding(category="security"),
        ) is True

    def test_secret_fixed_detected(self):
        assert ffa._finding_still_present(
            _CLEAN_CONTENT, "app.py", _sec_finding(category="security"),
        ) is False

    def test_security_without_category_still_uses_vanguard(self):
        # The /security-scan /fix endpoint passes rule_id only, no
        # category — Vanguard is the fallback, behaviour unchanged.
        assert ffa._finding_still_present(
            _SECRET_CONTENT, "app.py", _sec_finding(),
        ) is True
        assert ffa._finding_still_present(
            _CLEAN_CONTENT, "app.py", _sec_finding(),
        ) is False

    def test_full_scanner_id_rule_matches_vanguard_hit(self):
        # Health-endpoint shape: rule_id is the full scanner id
        # ("sec::<path>:<line>:<rule>"). The OLD exact-equality check
        # never matched this (Vanguard hit "name" != full id), so the
        # old code silently PASSED a still-present secret. The new
        # matcher closes that hole — this is a strengthening, not
        # just no-weakening.
        assert ffa._finding_still_present(
            _SECRET_CONTENT, "app.py",
            _sec_finding("sec::app.py:1:generic_api_key", category="security"),
        ) is True


# ─── (b) non-security dispatched to its own checker ───────────────────
class TestNonSecurityDispatchedToOwnChecker:
    def test_performance_unbounded_tolist_still_present_real_scanner(self):
        # Vanguard cannot see this rule at all — the old code returned
        # False here (and apply_finding_fix then "skipped" the finding
        # as already-absent without ever fixing it). The performance
        # checker must now catch it.
        assert ffa._finding_still_present(
            _PERF_STILL, "app.py",
            {"rule_id": "unbounded_tolist", "category": "performance",
             "file": "app.py", "line": 1},
        ) is True

    def test_performance_unbounded_tolist_fixed_real_scanner(self):
        assert ffa._finding_still_present(
            _PERF_FIXED, "app.py",
            {"rule_id": "unbounded_tolist", "category": "performance",
             "file": "app.py", "line": 1},
        ) is False

    def test_dependencies_cve_still_present_real_scanner(self, monkeypatch):
        # Cold OSV cache so the static _CVE_DB is authoritative and
        # no network is possible even in principle.
        monkeypatch.setattr(core, "_OSV_CACHE", {})
        assert ffa._finding_still_present(
            _DEP_STILL, "requirements.txt",
            {"rule_id": "CVE-2023-32681", "category": "dependencies",
             "file": "requirements.txt", "line": 1},
        ) is True

    def test_dependencies_cve_fixed_real_scanner(self, monkeypatch):
        monkeypatch.setattr(core, "_OSV_CACHE", {})
        assert ffa._finding_still_present(
            _DEP_FIXED, "requirements.txt",
            {"rule_id": "CVE-2023-32681", "category": "dependencies",
             "file": "requirements.txt", "line": 1},
        ) is False

    def test_category_derived_from_scanner_id_prefix_when_no_category(self):
        # The single-finding /fix endpoints pass no category but the
        # rule_id carries the "perf::" prefix — dispatch must still
        # reach the performance checker, not Vanguard.
        assert ffa._finding_still_present(
            _PERF_STILL, "app.py",
            {"rule_id": "perf::app.py:1:unbounded_tolist",
             "file": "app.py", "line": 1},
        ) is True
        assert ffa._finding_still_present(
            _PERF_FIXED, "app.py",
            {"rule_id": "perf::app.py:1:unbounded_tolist",
             "file": "app.py", "line": 1},
        ) is False

    def test_stubbed_category_checker_decides_not_vanguard(self, monkeypatch):
        # Routing proof: Vanguard is stubbed to see NOTHING, the
        # stubbed performance checker says the rule fires -> True
        # proves the category checker (not Vanguard) made the call.
        calls = []

        def fake_perf(text_cache):
            calls.append(dict(text_cache))
            return [{"title": "unbounded_tolist", "id": "perf::app.py:1:unbounded_tolist"}]

        monkeypatch.setitem(core.SCANNERS, "performance", fake_perf)
        monkeypatch.setattr(
            "services.vanguard_scanner.scan_text", lambda *a, **k: [])
        assert ffa._finding_still_present(
            _PERF_STILL, "app.py",
            {"rule_id": "unbounded_tolist", "category": "performance",
             "file": "app.py", "line": 1},
        ) is True
        assert calls == [{"app.py": _PERF_STILL}]

    def test_stubbed_category_checker_clean_means_fixed(self, monkeypatch):
        monkeypatch.setitem(core.SCANNERS, "performance", lambda tc: [])
        monkeypatch.setattr(
            "services.vanguard_scanner.scan_text", lambda *a, **k: [])
        assert ffa._finding_still_present(
            _PERF_FIXED, "app.py",
            {"rule_id": "unbounded_tolist", "category": "performance",
             "file": "app.py", "line": 1},
        ) is False


# ─── (c) unknown / missing category falls back to Vanguard ───────────
class TestUnknownCategoryFallsBackToVanguard:
    def test_unknown_category_secret_still_present(self):
        assert ffa._finding_still_present(
            _SECRET_CONTENT, "app.py",
            _sec_finding(category="not_a_real_category"),
        ) is True

    def test_unknown_category_clean_is_fixed(self):
        assert ffa._finding_still_present(
            _CLEAN_CONTENT, "app.py",
            _sec_finding(category="not_a_real_category"),
        ) is False

    def test_security_headers_category_routes_to_vanguard(self):
        # security_headers is deliberately Vanguard-only: its detector
        # fires iff NO header signal exists ANYWHERE in the repo, so a
        # single-file cache cannot prove the fix. Proof of the routing:
        # a Vanguard-visible rule filed under this category must still
        # be caught by Vanguard — if it were routed to the headers
        # scanner instead, that scanner would find no header signal
        # match for "generic_api_key" and wrongly return False.
        assert ffa._finding_still_present(
            _SECRET_CONTENT, "app.py",
            {"rule_id": "generic_api_key",
             "category": "security_headers", "file": "app.py", "line": 1},
        ) is True

    def test_security_headers_missing_rule_accepted_like_before(self):
        # ...and a header-less app file with NO secret is accepted,
        # exactly as the old Vanguard-only code accepted it (Vanguard
        # cannot see http_headers_missing). Documented limitation,
        # not a regression: the fix is taken on the LLM's word.
        assert ffa._finding_still_present(
            "from fastapi import FastAPI\napp = FastAPI()\n", "app.py",
            {"rule_id": "http_headers_missing",
             "category": "security_headers", "file": "app.py", "line": 1},
        ) is False


# ─── (d) no weakening vs the old Vanguard-only code ───────────────────
class TestNoWeakeningVsVanguardOnly:
    def test_old_caught_security_finding_still_caught(self):
        # The exact case the old code handled: bare Vanguard rule name,
        # secret still in the file.
        assert ffa._finding_still_present(
            _SECRET_CONTENT, "app.py", _sec_finding(),
        ) is True
        assert ffa._finding_still_present(
            _EVAL_CONTENT, "app.py",
            {"rule_id": "eval_usage", "file": "app.py", "line": 1},
        ) is True

    def test_vanguard_backstop_catches_mislabelled_category(self):
        # Category says "performance" but the rule is really a
        # Vanguard rule (eval_usage) still present in the file. The
        # performance checker finds nothing — the Vanguard backstop
        # must still catch it, or this would be weaker than old.
        assert ffa._finding_still_present(
            _EVAL_CONTENT, "app.py",
            {"rule_id": "eval_usage", "category": "performance",
             "file": "app.py", "line": 1},
        ) is True

    def test_vanguard_stub_hit_is_caught_whatever_the_category(self, monkeypatch):
        # Direct old-behaviour simulation: Vanguard (stubbed) reports
        # the rule, the category checker (stubbed) reports nothing.
        # Old code returned True; new code must too.
        monkeypatch.setitem(core.SCANNERS, "code_quality", lambda tc: [])
        monkeypatch.setattr(
            "services.vanguard_scanner.scan_text",
            lambda *a, **k: [{"name": "eval_usage", "line": 1}])
        assert ffa._finding_still_present(
            _EVAL_CONTENT, "app.py",
            {"rule_id": "eval_usage", "category": "code_quality",
             "file": "app.py", "line": 1},
        ) is True

    def test_category_checker_exception_falls_back_to_vanguard(self, monkeypatch):
        # A crashing category checker must not crash the validation
        # and must not falsely reject: the old Vanguard verdict
        # decides (clean -> False here).
        def boom(text_cache):
            raise RuntimeError("scanner exploded")

        monkeypatch.setitem(core.SCANNERS, "performance", boom)
        monkeypatch.setattr(
            "services.vanguard_scanner.scan_text", lambda *a, **k: [])
        assert ffa._finding_still_present(
            _PERF_FIXED, "app.py",
            {"rule_id": "unbounded_tolist", "category": "performance",
             "file": "app.py", "line": 1},
        ) is False

    def test_vanguard_exception_still_refuses_patch(self, monkeypatch):
        # Old fail-closed contract preserved: if Vanguard itself
        # cannot run, refuse the patch.
        monkeypatch.setattr(
            "services.vanguard_scanner.scan_text",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert ffa._finding_still_present(
            _CLEAN_CONTENT, "app.py", _sec_finding(),
        ) is True

    def test_empty_rule_id_trusts_llm_unchanged(self):
        assert ffa._finding_still_present(
            _SECRET_CONTENT, "app.py", {"file": "app.py"},
        ) is False


# ─── matcher unit checks ──────────────────────────────────────────────
class TestRuleMatcher:
    def test_bare_name_matches_full_scanner_id_hit(self):
        assert ffa._rule_matches_target(
            {"id": "perf::app.py:1:unbounded_tolist",
             "title": "unbounded_tolist"}, "unbounded_tolist") is True

    def test_full_scanner_id_target_matches_bare_name_hit(self):
        assert ffa._rule_matches_target(
            {"name": "generic_api_key"},
            "sec::app.py:1:generic_api_key") is True

    def test_cve_target_matches_dependency_message(self):
        assert ffa._rule_matches_target(
            {"id": "dep::requirements.txt:1:requests",
             "title": "vulnerable: requests==2.28.0",
             "message": "requests==2.28.0 \u2192 CVE-2023-32681 \u2014 upgrade required."},
            "CVE-2023-32681") is True

    def test_different_rule_does_not_match(self):
        assert ffa._rule_matches_target(
            {"title": "high_cap_tolist"}, "unbounded_tolist") is False
        assert ffa._rule_matches_target(
            {"name": "eval_usage"}, "exec_usage") is False

    @pytest.mark.parametrize("prefix,category", [
        ("sec::", "security"), ("perf::", "performance"),
        ("q::", "code_quality"), ("dep::", "dependencies"),
        ("db::", "database"), ("bh::", "bug_hunt"),
        ("docker::", "docker"), ("test::", "test_coverage"),
        ("apisec::", "api_security"), ("hdr::", "security_headers"),
        ("a11y::", "accessibility"), ("seo::", "seo_basics"),
        ("git::", "git_hygiene"), ("err::", "error_handling"),
        ("mobile::", "mobile_responsiveness"), ("docs::", "documentation"),
    ])
    def test_id_prefix_derives_every_category(self, prefix, category):
        assert ffa._category_for_finding(
            {"rule_id": f"{prefix}some/path.py:1:whatever"}, "") == category

    def test_explicit_category_wins_over_prefix(self):
        assert ffa._category_for_finding(
            {"category": "performance",
             "rule_id": "sec::app.py:1:generic_api_key"}, "") == "performance"

    def test_no_category_no_prefix_is_empty(self):
        assert ffa._category_for_finding({"rule_id": "eval_usage"}, "") == ""
