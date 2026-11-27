"""
services/vanguard_config.py — per-mode Vanguard verify settings.

Storage: single Mongo doc in `app_config`:

    {
      _id: "vanguard",
      enabled: bool,                                     # master switch
      levels:  { swift: "OFF" | "LOW" | "MEDIUM" | "HIGH", ... },
      updated_at: datetime,
      updated_by: user_id,
    }

Levels (2026-11-27 Vanguard 28):
  OFF    — truly off: no regex, no LLM, no E2B (audit log only).
  LOW    — vendor-shaped secret rules only (real credential shapes).
  MEDIUM — full R1 + R2 CRITICAL/HIGH + LLM CRITICAL block + E2B.
  HIGH   — full R1 + R2 (all) + chains + LLM HIGH+ block + E2B.

Back-compat: stored legacy levels map CRITICAL → LOW, HIGH → MEDIUM
(the old threshold names; the new HIGH is stricter than the old
HIGH). Unknown levels fall back to the per-mode default.

Reads are best-effort and self-healing: when the doc is missing we
fall back to the env-var defaults compiled at import time so the
verify pipeline never gates on infra hiccups.  A tiny in-memory TTL
cache (~10 s) keeps the per-commit overhead at one Mongo lookup per
batch of ships rather than one per file.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Defaults (used when no Mongo doc exists yet) ─────────────────────
_DEFAULT_ENABLED = (
    os.environ.get("VANGUARD_VERIFY_ENABLED", "1").lower()
    in ("1", "true", "yes", "on")
)
# 2026-11-27 — per-mode defaults (swift=MEDIUM, pro=HIGH, maxx=HIGH).
# The env var still overrides every mode when set (operator escape
# hatch); an invalid env value is ignored, not fatal.
_ENV_LEVEL = (
    os.environ.get("VANGUARD_VERIFY_BLOCK_LEVEL", "") or ""
).upper()
_VALID_LEVELS = ("OFF", "LOW", "MEDIUM", "HIGH")
_VALID_MODES  = ("swift", "pro", "maxx")
# Legacy level names (pre-2026-11-27) → current names. The old
# threshold names map to the new tier with the same blocking
# threshold: CRITICAL-only blocking is the weakest tier (LOW),
# CRITICAL+HIGH blocking is the middle tier (MEDIUM).
_LEGACY_LEVELS = {"CRITICAL": "LOW", "HIGH": "MEDIUM"}
_DEFAULT_LEVELS = {"swift": "MEDIUM", "pro": "HIGH", "maxx": "HIGH"}


def _default_level(mode: str) -> str:
    """Per-mode default level: env override wins, else the mode default."""
    if _ENV_LEVEL in _VALID_LEVELS:
        return _ENV_LEVEL
    return _DEFAULT_LEVELS.get((mode or "").lower(), "MEDIUM")


# Back-compat alias: the old single default (used by default_config
# callers that do not pass a mode). Resolves to the swift default.
_DEFAULT_LEVEL = _default_level("swift")
_DOC_ID       = "vanguard"

# 10-second cache so a burst of 50 file edits inside one commit
# doesn't hit Mongo 50 times for the same setting.
_CACHE_TTL_S  = 10.0
_cache: dict = {"ts": 0.0, "doc": None}


def _normalise_level(value, mode: str) -> str:
    """Map a stored/env level to a current valid level. Legacy names
    (CRITICAL/HIGH) map to LOW/MEDIUM; unknown values fall back to
    the mode's default (never to a weaker tier by accident)."""
    v = (value or "").upper() if isinstance(value, str) else ""
    v = _LEGACY_LEVELS.get(v, v)
    if v in _VALID_LEVELS:
        return v
    return _default_level(mode)


def default_config() -> dict:
    """Hard-coded fallback used when there's no DB doc yet."""
    return {
        "enabled":    _DEFAULT_ENABLED,
        "levels":     {m: _default_level(m) for m in _VALID_MODES},
        "updated_at": None,
        "updated_by": None,
    }


async def _load_doc() -> dict:
    """Fetch the singleton with TTL caching. Never raises."""
    now = time.monotonic()
    if _cache["doc"] is not None and (now - _cache["ts"]) < _CACHE_TTL_S:
        return _cache["doc"]
    from cto_services.db import get_db
    db = get_db()
    doc: Optional[dict] = None
    if db is not None:
        try:
            doc = await db.app_config.find_one({"_id": _DOC_ID})
        except Exception as e:
            logger.warning("vanguard_config load failed: %r", e)
    if not doc:
        doc = default_config()
    else:
        # Normalise — keep only the keys we own + valid levels.
        levels_in = (doc.get("levels") or {})
        levels    = {}
        for m in _VALID_MODES:
            levels[m] = _normalise_level(levels_in.get(m), m)
        doc = {
            "enabled":    bool(doc.get("enabled", _DEFAULT_ENABLED)),
            "levels":     levels,
            "updated_at": doc.get("updated_at"),
            "updated_by": doc.get("updated_by"),
        }
    _cache["doc"] = doc
    _cache["ts"]  = now
    return doc


async def get_config() -> dict:
    """Public read — returns the full config dict."""
    return await _load_doc()


async def get_mode_settings(mode: str) -> tuple[bool, str]:
    """Resolve `(enabled, block_level)` for a given Swift/Pro/Maxx mode.
    Unknown modes default to the strictest level (HIGH) so a typo
    never accidentally weakens enforcement. Legacy stored levels are
    normalised (CRITICAL → LOW, HIGH → MEDIUM) by _load_doc."""
    cfg = await _load_doc()
    if not cfg.get("enabled", True):
        return False, "OFF"
    mode_key = (mode or "").lower()
    if mode_key not in _VALID_MODES:
        return True, "HIGH"
    level = _normalise_level((cfg["levels"] or {}).get(mode_key), mode_key)
    return True, level


async def save_config(
    *,
    enabled: bool,
    levels:  dict,
    updated_by: Optional[str] = None,
) -> dict:
    """Upsert the singleton. Returns the normalised stored value."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise RuntimeError("database unavailable")

    safe_levels: dict[str, str] = {}
    for m in _VALID_MODES:
        safe_levels[m] = _normalise_level(levels.get(m), m)

    doc = {
        "_id":        _DOC_ID,
        "enabled":    bool(enabled),
        "levels":     safe_levels,
        "updated_at": datetime.now(timezone.utc),
        "updated_by": updated_by,
    }
    await db.app_config.update_one(
        {"_id": _DOC_ID}, {"$set": doc}, upsert=True,
    )
    # Bust the cache so the next verify call sees the change instantly.
    _cache["doc"] = None
    _cache["ts"]  = 0.0
    doc.pop("_id", None)
    return doc
