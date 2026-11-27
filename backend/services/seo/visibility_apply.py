"""services/seo/visibility_apply.py \u2014 Visibility Kit apply (compatibility delegate).

This module is a compatibility delegate and the implementation lives in
``services/seo/kit_engine.py`` (``run_visibility_kit``). Kept so existing
callers importing ``apply_visibility_kit`` by name keep working, with
exactly one live implementation.
"""
from __future__ import annotations

from services.seo.kit_engine import (
    ADVISORY_ITEMS,
    IMPLEMENTED_AUTO_ITEMS,
    KitOptions,
    run_visibility_kit,
)

__all__ = ["ADVISORY_ITEMS", "IMPLEMENTED_AUTO_ITEMS", "apply_visibility_kit"]


async def apply_visibility_kit(
    db,
    *,
    project: dict,
    requested_items: list[str],
    token: str,
    scan_urls: list[dict],
    site_meta: dict,
    bot_policy: dict,
    force: bool = False,
) -> dict:
    """Delegate to :func:`services.seo.kit_engine.run_visibility_kit`."""
    return await run_visibility_kit(
        db,
        project=project,
        requested_items=requested_items,
        token=token,
        scan_urls=scan_urls,
        site_meta=site_meta,
        bot_policy=bot_policy,
        options=KitOptions(force=force),
    )
