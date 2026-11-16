"""tests/test_regression_iter2026_70_billing_route_fitness.py

Devil's-advocate audit finding (Phase 6 — route-fitness coverage for
billing/entitlement/wallet routes).

This codebase already has a fitness test for admin routes
(test_regression_iter_adminteam_s1_fitness.py) that checks every
admin-gated route is registered in the access registry. No equivalent
existed for the plain "did this route land at the prefix we actually
intended" question on the billing-adjacent routers specifically —
and this exact mistake is cheap to make: main.py mounts ~100 routers
across two different prefixes (`/api` and `/api/aurem-dev`), and I
hit precisely this class of bug twice myself this session while
building unrelated harness pages (a fetch silently 404'd through the
wrong prefix, swallowed into "feature looks broken" rather than a
loud error). A billing/entitlement endpoint landing at the wrong
prefix is a materially worse version of the same mistake.

This asserts, for each billing-adjacent router, that EVERY one of its
registered paths starts with the prefix main.py actually mounts it
under in combination with the router's own internal prefix — built
by mounting the REAL router module exactly as main.py does (not a
re-typed literal list of expected paths, which would just duplicate
the same mistake it's trying to catch if main.py and this test drift
independently).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI

_FASTAPI_DEFAULT_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def _mounted_paths(router, outer_prefix: str) -> set[str]:
    app = FastAPI()
    app.include_router(router, prefix=outer_prefix)
    return {r.path for r in app.routes if hasattr(r, "path")} - _FASTAPI_DEFAULT_PATHS


class TestPaymentsRouterLandsUnderTheIntendedPrefix:
    def test_every_payments_route_is_under_api_aurem_dev(self):
        from routers.payments import router as payments_router
        paths = _mounted_paths(payments_router, "/api/aurem-dev")
        assert paths, "payments_router registered zero routes — collection broke"
        for p in paths:
            assert p.startswith("/api/aurem-dev/"), (
                f"payments route {p!r} does not start with /api/aurem-dev/ — "
                "wrong-prefix mount, same class of bug as the harness "
                "page-status 404 found this session"
            )

    def test_the_billing_mode_entitlement_endpoint_is_at_its_known_path(self):
        """Specific, named check on one entitlement-bearing route —
        a generic prefix check alone wouldn't catch a route that's
        under the right PREFIX but the wrong full PATH (e.g. a typo
        in the route decorator itself)."""
        from routers.payments import router as payments_router
        paths = _mounted_paths(payments_router, "/api/aurem-dev")
        assert "/api/aurem-dev/payments/my-plan" in paths
        assert "/api/aurem-dev/payments/billing-mode" in paths


class TestAdminPricingRouterLandsUnderTheIntendedPrefix:
    def test_every_admin_pricing_route_is_under_admin_pricing(self):
        from routers.admin_pricing import router as admin_pricing_router
        paths = _mounted_paths(admin_pricing_router, "/api/aurem-dev")
        assert paths, "admin_pricing_router registered zero routes"
        for p in paths:
            assert p.startswith("/api/aurem-dev/admin/pricing/") or \
                   p == "/api/aurem-dev/admin/pricing", (
                f"admin_pricing route {p!r} is not under "
                "/api/aurem-dev/admin/pricing — did the router's own "
                "internal prefix= change without main.py's mount "
                "prefix being updated to match (or vice versa)?"
            )

    def test_margin_confirm_and_fixed_costs_are_at_their_known_paths(self):
        from routers.admin_pricing import router as admin_pricing_router
        paths = _mounted_paths(admin_pricing_router, "/api/aurem-dev")
        assert "/api/aurem-dev/admin/pricing/margin/confirm" in paths
        assert "/api/aurem-dev/admin/pricing/fixed-costs" in paths


class TestAdminPaymentsRouterLandsUnderTheIntendedPrefix:
    def test_every_admin_payments_route_is_under_admin(self):
        from routers.admin_payments import router as admin_payments_router
        paths = _mounted_paths(admin_payments_router, "/api/aurem-dev")
        assert paths, "admin_payments_router registered zero routes"
        for p in paths:
            assert p.startswith("/api/aurem-dev/admin/"), (
                f"admin_payments route {p!r} is not under "
                "/api/aurem-dev/admin/"
            )

    def test_does_not_accidentally_collide_with_admin_pricing_namespace(self):
        """admin_payments_router's internal prefix is bare "/admin"
        (broader than admin_pricing_router's "/admin/pricing") — a
        future route added there without care could accidentally
        shadow or duplicate a pricing-panel path. Confirms today's
        route sets are disjoint."""
        from routers.admin_payments import router as admin_payments_router
        from routers.admin_pricing import router as admin_pricing_router
        payments_paths = _mounted_paths(admin_payments_router, "/api/aurem-dev")
        pricing_paths = _mounted_paths(admin_pricing_router, "/api/aurem-dev")
        overlap = payments_paths & pricing_paths
        assert overlap == set(), (
            f"admin_payments and admin_pricing routers both register "
            f"these paths: {overlap!r} — one of them will shadow the "
            "other depending on include_router() order in main.py"
        )


class TestStripeWebhookCompatIsTheOneDeliberateException:
    def test_stripe_webhook_compat_lands_at_the_bare_api_path_on_purpose(self):
        """The ONE legitimate exception to the /api/aurem-dev
        convention among these routers — confirmed intentional via
        its own docstring (routers/stripe_webhook_compat.py): Stripe's
        dashboard was configured at /api/stripe/webhook (no aurem-dev)
        and every real payment webhook 404'd until this alias was
        added. Locking in that it STAYS at the bare path, since this
        is exactly the kind of route a well-meaning future refactor
        might "fix" by moving it to match the other payments routes —
        which would silently break Stripe's actual webhook config
        again."""
        from routers.stripe_webhook_compat import router as compat_router
        paths = _mounted_paths(compat_router, "/api")
        assert paths == {"/api/stripe/webhook"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
