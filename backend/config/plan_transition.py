"""
config/plan_transition.py — Master Prompt v2 PART 6.1/6.2.

Config constants for the (not-yet-real) task-plan → token-plan
transition. NONE of these are active — PART 6 builds a DRY-RUN tool
only; no real user's wallet, tier, or entitlement is ever written by
anything in this module or services/plan_transition.py.

LEGACY_TASK_TO_TOKEN_RATE is intentionally None by default: per your
instruction, "if no rate is configured, the tool lists the user as
'needs decision' instead of picking a number" — this constant is
never auto-guessed.
"""
from __future__ import annotations

import os


def _float_env(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Tokens granted per 1 remaining old-plan "task" of allowance for a
# PAYING user. None (unset) by design — see module docstring. A real
# value must be an explicit founder decision, never inferred here.
LEGACY_TASK_TO_TOKEN_RATE: float | None = _float_env("LEGACY_TASK_TO_TOKEN_RATE")

# Free users: days of grace after cutover before "free plan becomes
# free-scan-only + fix locked behind payment" takes effect.
FREE_PLAN_GRACE_DAYS: int = _int_env("FREE_PLAN_GRACE_DAYS", 30)
