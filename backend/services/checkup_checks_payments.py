"""
services/checkup_checks_payments.py — Check Library v2, layer 5 (payments):
L5.3 webhook without signature check, L5.4 webhook not idempotent,
L5.5 paid status set from the checkout redirect.  Detection only — fixes
are plan_first and never touch payment code automatically.

Zero-false-alarm stance (C3): verification or de-duplication that lives in
another file is looked for repo-wide; if it exists somewhere we can't tie
to the handler, the item is `unverified` (couldn't confirm), not red.
"""
from __future__ import annotations

import re
from typing import Iterator

from services.checkup_common import _files, _item, _line_of

_CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_SKIP = re.compile(r"(?:^|/)(?:tests?|__tests__|fixtures?|examples?|docs?)/|\.(?:test|spec)\.|(?:^|/)test_", re.I)
_PROVIDER = re.compile(r"stripe|razorpay|paypal|lemon\s?squeezy|paddle", re.I)
_WEBHOOK_ROUTE = re.compile(r"(?:\.post|\.route|\.all)\(\s*[\"'`][^\"'`]*webhook|@\w+\.(?:post|route)\(\s*[\"'][^\"']*webhook", re.I)
_VERIFY = re.compile(
    r"constructEvent|construct_event|validate_?webhook_?signature|verify_?webhook|webhook\w*verif|verif\w*(?:signature|webhook)"
    r"|timingSafeEqual|compare_digest|\bsvix\b|x-razorpay-signature|stripe-signature|signature\s*(?:!==?|===?)", re.I)
_DEDUPE = re.compile(
    r"idempoten|already[_ -]?(?:processed|handled|seen)|processed[_ ]?events?|(?:webhook|stripe|payment|razorpay)_?events?\b"
    r"|dedup|on conflict|event_?id\s+(?:text|varchar|uuid)[^\n]*unique", re.I)
_STATE_WRITE = re.compile(
    r"\.(?:update|insert|create|save|upsert|updateOne|findOneAndUpdate|insertOne)\(|\bUPDATE\s+\w+|\bINSERT\s+INTO|supabase\.from\(", re.I)
_SUCCESS_PATH = re.compile(r"success|thank[-_ ]?you|payment[-_]?return|checkout[-_]?return|(?:^|/)return(?:\.|/)", re.I)
_PAID_WRITE = re.compile(
    r"(?:is_?pro|is_?premium|is_?paid|subscribed|subscription_?(?:status|active)|plan|tier)\s*[:=]\s*"
    r"(?:true|[\"'](?:active|pro|premium|paid|business|team)[\"'])", re.I)
_SERVER_CONFIRM = re.compile(r"sessions\.retrieve|checkout\.Session\.retrieve|paymentIntents?\.retrieve|PaymentIntent\.retrieve|"
                             r"subscriptions\.retrieve|payment\.fetch|verify_?payment|verify_?signature", re.I)


def _is_webhook_handler(path: str, text: str) -> bool:
    return bool(_PROVIDER.search(text)) and (bool(re.search(r"webhook", path, re.I)) or bool(_WEBHOOK_ROUTE.search(text)))


def _handlers(tc: dict[str, str]) -> Iterator[tuple[str, str]]:
    for path, text in _files(tc):
        if path.endswith(_CODE_EXT) and not _SKIP.search(path) and _is_webhook_handler(path, text):
            yield path, text


def _repo_has(tc: dict[str, str], rx: re.Pattern, exclude: str) -> bool:
    return any(rx.search(t) for p, t in _files(tc) if p != exclude and not _SKIP.search(p))


def _first_line(text: str, rx: re.Pattern) -> int:
    m = rx.search(text) or _WEBHOOK_ROUTE.search(text)
    return _line_of(text, m.start()) if m else 1


def check_webhook_signature(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _handlers(tc):
        if _VERIFY.search(text):
            continue
        elsewhere = _repo_has(tc, _VERIFY, path)
        out.append(_item(
            scanner="payments", rule_id="webhook_unsigned", severity="critical",
            title="Anyone can pretend to be your payment provider and mark orders as paid",
            impact="Without checking the provider's signature, a stranger can send a fake 'payment received' message and get your product or credit for free.",
            file=path, line=_first_line(text, _PROVIDER),
            detail="payment webhook handler found, no signature verification in it",
            confidence="unverified" if elsewhere else "high",
            fix_hint="Verify the provider's signature on the raw request body before trusting any event."))
    return out


def check_webhook_idempotency(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _handlers(tc):
        if not _VERIFY.search(text) or not _STATE_WRITE.search(text) or _DEDUPE.search(text):
            continue                      # unsigned = L5.3's finding; read-only/deduped = fine
        if _repo_has(tc, _DEDUPE, path):
            continue
        out.append(_item(
            scanner="payments", rule_id="webhook_not_idempotent", severity="high",
            title="A repeated payment message can charge, credit or email your customer twice",
            impact="Payment providers resend events; without remembering which ones were handled, customers can get double credits or duplicate orders.",
            file=path, line=_first_line(text, _STATE_WRITE),
            detail="webhook handler changes data and no record of already-processed event ids was found",
            confidence="medium",
            fix_hint="Store each processed event id (unique) and skip events you have already handled."))
    return out


def check_paid_from_redirect(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if (not path.endswith(_CODE_EXT) or _SKIP.search(path) or re.search(r"webhook", path, re.I)
                or not _SUCCESS_PATH.search(path) or not _PROVIDER.search(text)):
            continue
        m = _PAID_WRITE.search(text)
        if m and not _SERVER_CONFIRM.search(text):
            out.append(_item(
                scanner="payments", rule_id="paid_set_from_redirect", severity="high",
                title="Visiting the 'payment successful' page can unlock paid features without paying",
                impact="Anyone who opens that page's address directly can be marked as a paying customer.",
                file=path, line=_line_of(text, m.start()),
                detail="paid status is written on a checkout success/return page without asking the provider to confirm the payment",
                fix_hint="Only mark an account as paid from the verified webhook (or after confirming the session with the provider on the server)."))
    return out
