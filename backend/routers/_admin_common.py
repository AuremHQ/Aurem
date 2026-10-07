"""_admin_common.py — shared helpers used by every admin sub-router.

Admin Team S2 (AT1b) — the actual decision lives in ONE place,
`cto_services.auth._finish_admin_check` (D19). This wrapper still calls
ITS OWN local `current_dev` (not `cto_services.auth.require_admin`
end-to-end) because ~40 existing tests patch
`routers._admin_common.current_dev` to fake authentication for every
router that imports `_require_admin` from here (e.g.
test_phase2c_admin_analytics_router.py, test_phase2_admin_users_coverage.py)
— patching a name this module doesn't call would silently stop working.
Keeping the local call preserves that seam with zero test edits, while
`_finish_admin_check` keeps the real RULE in exactly one place.
"""
from __future__ import annotations

from typing import Optional

from cto_services.auth import current_dev, _finish_admin_check


async def _require_admin(authorization: Optional[str], request=None) -> dict:
    user = await current_dev(authorization)
    return await _finish_admin_check(user, request)


__all__ = ["_require_admin"]
