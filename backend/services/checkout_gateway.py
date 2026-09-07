"""
services/checkout_gateway.py — dependency inversion for Stripe checkout.

Services (confirm_execution, actions/pending_action) used to import
`routers.payments.create_checkout_session` directly — a service→router
dependency (architecture boundary: service-imports-router). The Stripe
implementation stays in routers/payments.py (it owns the price map,
preflight and timeouts); it REGISTERS itself here at import time and
services call through this seam instead.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

CheckoutFn = Callable[[dict, str], Awaitable[dict]]

_provider: Optional[CheckoutFn] = None


def register_checkout_provider(fn: CheckoutFn) -> None:
    global _provider
    _provider = fn


async def create_checkout_session(user: dict, plan: str) -> dict:
    if _provider is None:
        raise RuntimeError("checkout provider not registered (routers.payments not loaded)")
    return await _provider(user, plan)
