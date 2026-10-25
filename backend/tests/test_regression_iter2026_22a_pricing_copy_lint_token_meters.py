"""tests/test_regression_iter2026_22a_pricing_copy_lint_token_meters.py

ITEM 6 (Medium) — independent-review fix round.

STEP (a) sweep finding: "No IDE, no token meters." (Landing.jsx hero
headline) and two sibling hits ("Flat pricing. No token meters."
Pricing.jsx h1; "No token meters —..." Pricing.jsx/Landing.jsx detail
paragraphs) were UNGATED — plain string literals, never behind
TOKEN_WORDING_ACTIVE — and none of scripts/pricing_copy_lint.py's
existing _LEGACY_TASK_WORDING_PATTERNS caught "token meters" (the
existing patterns match "token billing"/"token metering", a different
word). This is both a copy bug (STEP b: now gated via
lib/billingCopy.js's heroTokenMetersClaim/pricingHeroHeadline/
pricingDetailLine/landingPricingSectionSub/heroStatTokenBilling) and a
lint gap (STEP b: new `no\\s+token\\s+meters?` pattern added).
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture
def lint_module():
    import scripts.pricing_copy_lint as mod
    importlib.reload(mod)
    return mod


def _hits(lint_module, text: str, *, mode: str):
    os.environ["TOKEN_BILLING_MODE"] = mode
    return lint_module._scan_legacy_task_wording(text, mode=lint_module._current_mode())


class TestNoTokenMetersPatternAdded:
    def test_no_token_meters_caught_at_mode_on(self, lint_module):
        hits = _hits(lint_module, "No IDE, no token meters.", mode="on")
        assert hits, "the new 'no token meters' pattern must catch this exact phrase"

    def test_no_token_meters_caught_at_mode_shadow(self, lint_module):
        hits = _hits(lint_module, "Flat pricing. No token meters.", mode="shadow")
        assert hits

    def test_no_token_meters_allowed_at_mode_off(self, lint_module):
        hits = _hits(lint_module, "No IDE, no token meters.", mode="off")
        assert hits == [], "legacy text is still accurate while mode stays off"

    def test_pre_existing_token_billing_pattern_still_works_unweakened(self, lint_module):
        hits = _hits(lint_module, "no token billing, ever", mode="on")
        assert hits, "must not have been weakened while adding the new pattern"


class TestRealFilesNoLongerHardcodeTheLegacyClaim:
    """Landing.jsx/Pricing.jsx are EXEMPT from the line-scan once they
    import lib/billingCopy (same precedent as every other gated page
    — billingCopy.js is the one place both wordings are meant to
    live). What actually proves STEP (b)'s fix is that the HARDCODED
    literal is gone from these files' source, replaced by a call into
    the shared, mode-aware module — not a second regex scan of text
    the design already intends to exempt."""

    def test_landing_no_longer_hardcodes_the_hero_claim(self, lint_module):
        root = Path(lint_module.ROOT)
        text = (root / "frontend" / "src" / "pages" / "Landing.jsx").read_text(encoding="utf-8")
        assert "lib/billingCopy" in text
        assert "heroTokenMetersClaim" in text
        assert "<span>No IDE, no token meters.</span>" not in text
        assert "<div className=\"stat-num\">0</div><div className=\"stat-label\">token billing</div>" not in text

    def test_pricing_no_longer_hardcodes_the_headline(self, lint_module):
        root = Path(lint_module.ROOT)
        text = (root / "frontend" / "src" / "pages" / "Pricing.jsx").read_text(encoding="utf-8")
        assert "lib/billingCopy" in text
        assert "pricingHeroHeadline" in text
        assert "Flat pricing. No token meters." not in text
        assert "<h1" in text and "{pricingHeroHeadline()}" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
