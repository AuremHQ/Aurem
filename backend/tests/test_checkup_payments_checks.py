"""Check Library v2 / Group 4 (L5.3, L5.4, L5.5): fires on bad fixtures, silent on clean."""
from __future__ import annotations

from services.checkup_checks_payments import (
    check_paid_from_redirect, check_webhook_idempotency, check_webhook_signature)

UNSIGNED = (
    "import Stripe from 'stripe';\n"
    "app.post('/api/stripe/webhook', express.json(), async (req, res) => {\n"
    "  const event = req.body;\n"
    "  if (event.type === 'checkout.session.completed') await db.users.update({ paid: true });\n"
    "  res.json({ ok: true });\n});\n")
SIGNED = (
    "import Stripe from 'stripe';\n"
    "app.post('/api/stripe/webhook', express.raw({type: 'application/json'}), async (req, res) => {\n"
    "  const event = stripe.webhooks.constructEvent(req.body, req.headers['stripe-signature'], secret);\n"
    "  const seen = await db.processed_events.findOne({ id: event.id });\n"
    "  if (seen) return res.json({ ok: true });\n"
    "  await db.users.update({ paid: true });\n  await db.processed_events.insert({ id: event.id });\n});\n")
SIGNED_NO_DEDUPE = SIGNED.replace("processed_events", "log").replace("  if (seen) return res.json({ ok: true });\n", "")
PY_SIGNED = (
    "@router.post('/razorpay/webhook')\n"
    "async def hook(request):\n"
    "    body = await request.body()\n"
    "    razorpay_client.utility.verify_webhook_signature(body, sig, secret)\n"
    "    await db.orders.update_one({'id': 1}, {'$set': {'paid': True}})\n")


# ── L5.3 ──────────────────────────────────────────────────────────────
def test_unsigned_webhook_fires_critical():
    [item] = check_webhook_signature({"server/stripe.js": UNSIGNED})
    assert item["rule_id"] == "webhook_unsigned" and item["severity"] == "critical"
    assert item["confidence"] == "high" and item["evidence"]["line"] >= 1


def test_signed_webhooks_silent_node_and_python():
    assert check_webhook_signature({"server/stripe.js": SIGNED, "api/rz.py": PY_SIGNED}) == []


def test_verification_in_another_file_is_unverified_not_red():
    tc = {"server/stripe.js": UNSIGNED, "lib/verify.js": "export const verifyWebhookSignature = () => timingSafeEqual(a, b);"}
    [item] = check_webhook_signature(tc)
    assert item["confidence"] == "unverified"


def test_non_payment_or_test_webhooks_silent():
    slack = "app.post('/slack/webhook', (req, res) => res.send('ok'));\n"
    assert check_webhook_signature({"server/slack.js": slack, "tests/stripe_webhook.test.js": UNSIGNED,
                                    "docs/webhook.js": UNSIGNED}) == []


# ── L5.4 ──────────────────────────────────────────────────────────────
def test_signed_but_not_idempotent_fires_high():
    [item] = check_webhook_idempotency({"server/stripe.js": SIGNED_NO_DEDUPE})
    assert item["rule_id"] == "webhook_not_idempotent" and item["severity"] == "high"


def test_idempotent_or_unsigned_or_readonly_silent():
    readonly = SIGNED_NO_DEDUPE.replace("  await db.users.update({ paid: true });\n", "")
    assert check_webhook_idempotency({"a/stripe.js": SIGNED}) == []
    assert check_webhook_idempotency({"a/stripe.js": UNSIGNED}) == []      # L5.3 owns this one
    assert check_webhook_idempotency({"a/stripe.js": readonly.replace("db.log.insert", "console.log")}) == []


def test_dedupe_table_in_migration_counts():
    tc = {"server/stripe.js": SIGNED_NO_DEDUPE, "supabase/migrations/001.sql": "create table processed_events (event_id text unique);"}
    assert check_webhook_idempotency(tc) == []


# ── L5.5 ──────────────────────────────────────────────────────────────
def test_paid_from_success_page_fires():
    page = "// stripe checkout return\nexport default async function Success({ searchParams }) {\n  await supabase.from('profiles').update({ plan: 'pro' });\n}\n"
    [item] = check_paid_from_redirect({"app/checkout/success/page.tsx": page})
    assert item["rule_id"] == "paid_set_from_redirect" and item["severity"] == "high"
    assert item["evidence"]["line"] == 3


def test_success_page_that_confirms_or_does_not_write_is_silent():
    confirm = "const s = await stripe.checkout.sessions.retrieve(id);\nawait db.update({ plan: 'pro' });\n"
    plain = "export default function Success(){ return <h1>Thanks! (stripe)</h1>; }\n"
    wh = "stripe\nawait db.update({ plan: 'pro' });\n"
    assert check_paid_from_redirect({"app/success/page.js": confirm, "app/thanks/success.js": plain,
                                     "api/stripe/webhook.js": wh, "src/pages/Home.js": wh}) == []
