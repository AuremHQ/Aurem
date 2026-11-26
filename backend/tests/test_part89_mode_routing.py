"""
tests/test_part89_mode_routing.py — Part IX audit fix (B):
mode_routing.py fail-closed defaults + input normalization.

Offline unit tests. Run from backend/ with the agent1 venv:
    ~/workspace/tmp/ora-fix/agent1-venv/bin/python -m pytest tests/test_part89_mode_routing.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from services.mode_routing import (  # noqa: E402
    needs_edit_upgrade_offer,
    resolve_model_mode,
)


@pytest.fixture(autouse=True)
def _gated_mode(monkeypatch):
    monkeypatch.delenv("EDIT_TIER_MODE", raising=False)


def test_no_flag_defaults_fail_closed():
    """The old account_has_pro=True default silently escalated a free
    account to Pro. No flag now means no Pro: mode stays Swift."""
    assert resolve_model_mode("agentic", "swift") == "swift"
    assert resolve_model_mode("agentic", "swift",
                              account_has_pro=False) == "swift"


def test_explicit_pro_flag_still_escalates():
    """An account that actually has Pro (explicit flag, like the sole
    production caller passes) still escalates, unchanged."""
    assert resolve_model_mode("agentic", "swift",
                              account_has_pro=True) == "pro"


def test_upgrade_offer_fires_when_no_flag():
    """With the fail-closed default, the no-flag call now reports the
    upgrade offer (gated mode) instead of silently escalating AND
    suppressing the offer."""
    assert needs_edit_upgrade_offer("agentic", "swift") is True
    assert needs_edit_upgrade_offer("agentic", "swift",
                                    account_has_pro=False) is True
    assert needs_edit_upgrade_offer("agentic", "swift",
                                    account_has_pro=True) is False


def test_tier_and_mode_normalized():
    """"Agentic" / " Swift " behave like "agentic" / "swift" —
    mirroring edit_tier_mode()'s own .strip().lower()."""
    assert resolve_model_mode("Agentic", "Swift",
                              account_has_pro=True) == "pro"
    assert resolve_model_mode(" agentic ", " swift ") == "swift"
    assert resolve_model_mode("AGENTIC", "SWIFT",
                              account_has_pro=True) == "pro"
    assert needs_edit_upgrade_offer("Agentic", " Swift ") is True


def test_normalized_non_swift_mode_returned_canonical():
    """A non-escalating mode comes back normalized, not verbatim."""
    assert resolve_model_mode("casual", " Pro ") == "pro"
    assert resolve_model_mode("agentic", " PRO ") == "pro"


def test_none_inputs_do_not_crash():
    assert resolve_model_mode(None, None) is not None
    assert needs_edit_upgrade_offer(None, None) is False


def test_transparent_mode_still_escalates_without_flag(monkeypatch):
    """EDIT_TIER_MODE=transparent keeps the old unconditional
    escalation even with the fail-closed default (founder opt-in)."""
    monkeypatch.setenv("EDIT_TIER_MODE", "transparent")
    assert resolve_model_mode("agentic", "swift") == "pro"
    assert needs_edit_upgrade_offer("agentic", "swift") is False
