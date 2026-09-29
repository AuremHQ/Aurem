"""
demo_project/sample_app.py — 2026-09-28 "ACTIVATION FIRST" Part 3 demo.

A tiny, SELF-CONTAINED sample used ONLY by the "Try a demo project"
starter choice (routers/onboarding.py::run_demo_project). No real user
data, no secrets, no external repo — this file is bundled directly in
our own codebase and is never written to or committed by a demo run.

Intentional bug: `discount_percent` is documented as "10 means 10%"
but the math treats it as a raw fraction, so a normal 10%-off coupon
silently makes the order total deeply negative.
"""


def calculate_order_total(subtotal: float, discount_percent: float, shipping: float) -> float:
    """Return the final order total after a percent-off coupon and flat shipping.

    `discount_percent` is meant to be a whole number, e.g. 10 for "10% off".
    """
    discount = subtotal * discount_percent  # BUG: should divide by 100
    total = subtotal - discount + shipping
    return round(total, 2)


if __name__ == "__main__":
    # A $50 cart with a "10% off" coupon and $5 shipping should total $50.
    print(calculate_order_total(subtotal=50, discount_percent=10, shipping=5))
