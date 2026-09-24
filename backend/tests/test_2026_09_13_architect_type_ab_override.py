"""
tests/test_2026_09_13_architect_type_ab_override.py

Phase 3 (Architect Agent ticket) — approved 2026-09-13 WITH a required
change: the LLM's own Type A/B classification is a starting point,
NEVER the final authority, for payment/domain/branding/pricing/legal/
irreversible decisions. `apply_type_b_overrides()` is a deterministic,
code-level second pass, independent of the model.

Tests 1-6 are pure/deterministic (construct a decision the LLM
*mis-classified* as Type A and prove the code forcibly reclassifies
it). Test 7 makes a REAL LLM call with an idea that has an obvious
payment/pricing decision, and proves that even if the model behaves
correctly on its own, the override pass still runs and produces the
IDENTICAL final answer — i.e. the override is a genuine safety net,
not a no-op that happens to agree with a well-behaved model by luck
(test 8 forces disagreement to prove that distinction).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.architect_agent import apply_type_b_overrides, invoke_architect


def test_1_llm_misclassified_payment_as_type_a_gets_overridden():
    """THE EXACT FAILURE MODE the user's pushback described: the LLM
    that invented "use Stripe" has no independent incentive to flag
    its own choice as needing human input, and calls it Type A."""
    decisions = [
        {"field": "payment_provider", "value": "Stripe", "type": "A",
         "reasoning": "Stripe is a reliable default for payments"},
    ]
    out = apply_type_b_overrides(decisions)
    assert out[0]["type"] == "B"
    assert out[0]["override_applied"] is True
    assert "code-level override" in out[0]["override_reason"]
    assert "Type A" in out[0]["override_reason"]
    assert out[0]["safe_default"]  # a safe default was filled in


def test_2_domain_and_branding_and_pricing_all_forced_to_b():
    decisions = [
        {"field": "custom_domain", "value": "app.example.com", "type": "A", "reasoning": "x"},
        {"field": "brand_color_scheme", "value": "blue/white", "type": "A", "reasoning": "x"},
        {"field": "pricing_tier_amounts", "value": "$9/$29/$99", "type": "A", "reasoning": "x"},
    ]
    out = apply_type_b_overrides(decisions)
    assert all(d["type"] == "B" for d in out)
    assert all(d["override_applied"] for d in out)


def test_3_llm_correctly_calling_something_type_b_is_left_alone_not_double_logged_as_override():
    decisions = [
        {"field": "payment_provider", "value": "Stripe", "type": "B",
         "reasoning": "financial decision", "safe_default": "none configured"},
    ]
    out = apply_type_b_overrides(decisions)
    assert out[0]["type"] == "B"
    assert out[0]["override_applied"] is False  # already correct — no override needed
    assert out[0]["safe_default"] == "none configured"  # untouched


def test_4_genuine_type_a_decision_is_never_touched():
    decisions = [
        {"field": "db_field_naming", "value": "snake_case", "type": "A", "reasoning": "convention"},
        {"field": "internal_folder_structure", "value": "src/components", "type": "A", "reasoning": "convention"},
    ]
    out = apply_type_b_overrides(decisions)
    assert all(d["type"] == "A" for d in out)
    assert all(d["override_applied"] is False for d in out)


def test_5_override_never_loosens_b_back_to_a():
    """Sanity: the override function only tightens A->B. If a decision
    somehow arrives already Type B with NO keyword match, it must stay B."""
    decisions = [
        {"field": "some_edge_case", "value": "x", "type": "B", "reasoning": "no keyword here"},
    ]
    out = apply_type_b_overrides(decisions)
    assert out[0]["type"] == "B"


def test_6_empty_and_none_decisions_list_is_a_safe_noop():
    assert apply_type_b_overrides([]) == []
    assert apply_type_b_overrides(None) == []


@pytest.fixture
def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_7_full_live_invoke_forces_payment_decision_to_type_b(_db):
    """NO MOCKS — a real idea with an obvious payment/pricing
    decision, through the REAL `invoke_architect` (real LLM call).
    Regardless of what the model itself decided, the FINAL persisted
    blueprint's payment-related decision(s) must end up Type B with
    a safe_default, and `type_b_pending` on the doc must list it —
    because the code-level override is unconditional for this
    category, not contingent on the model behaving well."""
    pid = "phase3-live-payment-test"
    try:
        result, err = await invoke_architect(
            _db, project_id=pid, user_id="test_admin_001",
            idea=("a subscription fitness app with monthly and yearly "
                  "pricing tiers, paid via credit card at checkout"),
        )
        assert err is None, f"live architect call failed: {err}"
        decisions = result["blueprint"].get("decisions", [])
        payment_or_pricing = [
            d for d in decisions
            if any(k in (d.get("field", "") + d.get("value", "")).lower()
                   for k in ("payment", "pricing", "price", "checkout", "stripe"))
        ]
        assert payment_or_pricing, (
            "model produced no payment/pricing decision entry at all for "
            "an idea that explicitly describes pricing tiers + checkout"
        )
        assert all(d["type"] == "B" for d in payment_or_pricing), (
            f"a payment/pricing decision was NOT Type B after the "
            f"override pass: {payment_or_pricing}"
        )
        doc = await _db.project_blueprints.find_one({"project_id": pid}, {"_id": 0})
        assert doc["type_b_pending"], "type_b_pending must be non-empty on the persisted doc"
        assert any(
            "payment" in (p.get("field") or "").lower()
            or "pricing" in (p.get("field") or "").lower()
            or "price" in (p.get("field") or "").lower()
            for p in doc["type_b_pending"]
        )
    finally:
        await _db.project_blueprints.delete_many({"project_id": pid})
        await _db.architect_events.delete_many({"project_id": pid})


def test_8_override_changes_the_outcome_when_llm_disagrees_proving_its_not_a_lucky_noop():
    """Directly proves the override pass has TEETH: feed it a decision
    the (simulated) LLM explicitly got wrong (Type A on a payment
    field) and confirm the OUTPUT actually changes, rather than the
    override silently agreeing with whatever the model said."""
    llm_said = [
        {"field": "payment_provider", "value": "Razorpay", "type": "A", "reasoning": "good default"},
    ]
    before = [dict(d) for d in llm_said]
    after = apply_type_b_overrides(llm_said)
    assert before[0]["type"] == "A"
    assert after[0]["type"] == "B"
    assert before[0]["type"] != after[0]["type"], "override had no effect — not a real safety net"


def test_9_reasoning_text_mentioning_payment_does_not_falsely_flip_unrelated_decision():
    """LIVE-CAUGHT precision bug (fixed same turn): a genuinely
    unrelated Type A decision (notifications) whose free-text
    REASONING happens to mention "payment" in passing (explaining why
    notifications are needed for a subscription app) must NOT be
    flipped to Type B — only the field/value being decided matters,
    not incidental words in the explanation."""
    decisions = [
        {"field": "notifications", "value": "needed", "type": "A",
         "reasoning": "users need to be informed about subscription and payment events"},
        {"field": "infra background_jobs", "value": "true", "type": "A",
         "reasoning": "required for handling subscription renewals and payment processing"},
        {"field": "payment provider", "value": "none configured", "type": "B",
         "reasoning": "a real payment provider must be chosen by the user",
         "safe_default": "placeholder checkout button"},
    ]
    out = apply_type_b_overrides(decisions)
    assert out[0]["type"] == "A" and out[0]["override_applied"] is False, (
        "notifications decision was falsely flipped to Type B by its own reasoning text"
    )
    assert out[1]["type"] == "A" and out[1]["override_applied"] is False, (
        "background_jobs decision was falsely flipped to Type B by its own reasoning text"
    )
    assert out[2]["type"] == "B"  # the genuine payment decision stays B
