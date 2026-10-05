"""
routers/admin_vanguard.py — admin UI for per-mode Vanguard config.

Two endpoints, both admin/founder-gated:

  GET  /admin/vanguard/config  →  current { enabled, levels, updated_at, updated_by }
  POST /admin/vanguard/config  →  upsert  { enabled, levels }

The body's `levels` key MUST be a dict of `{swift|pro|maxx: OFF|CRITICAL|HIGH}`.
Unknown modes are ignored; unknown levels coerce to the safest default
(CRITICAL) by the service layer.

The frontend at `/admin/vanguard` calls these to drive the three radio
pills + global master switch.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Depends
from cto_services.auth import require_admin_dep, current_dev, _finish_admin_check
from pydantic import BaseModel

from services.vanguard_config import get_config, save_config

router = APIRouter(prefix="/admin/vanguard", tags=["Admin / Vanguard"],
                   dependencies=[Depends(require_admin_dep)])  # Iter 358 router-level gate


async def _require_admin(me: dict) -> None:
    # Admin Team S2 (AT1b) — delegates to the one real decision function
    # (D19). `request=None` here falls back to `_current_request` (set by
    # the router-level `require_admin_dep` dependency moments earlier in
    # THIS same request), so this redundant inline check sees the exact
    # same route/registry context, not a weaker one.
    await _finish_admin_check(me, request=None)


@router.get("/config")
async def read_vanguard_config(
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    await _require_admin(me)
    cfg = await get_config()
    # `updated_at` may be a tz-aware datetime; ISO-string it for JSON.
    ts = cfg.get("updated_at")
    if hasattr(ts, "isoformat"):
        cfg["updated_at"] = ts.isoformat()
    return {"ok": True, "config": cfg}


class _ConfigBody(BaseModel):
    enabled: bool
    levels:  dict  # {swift|pro|maxx: OFF|CRITICAL|HIGH}


@router.post("/config")
async def write_vanguard_config(
    body: _ConfigBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    await _require_admin(me)
    saved = await save_config(
        enabled=body.enabled,
        levels=body.levels or {},
        updated_by=me.get("user_id"),
    )
    ts = saved.get("updated_at")
    if hasattr(ts, "isoformat"):
        saved["updated_at"] = ts.isoformat()
    return {"ok": True, "config": saved}
