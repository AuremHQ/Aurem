"""
services/trust_calibration_cron.py — scheduled, idempotent computation
of the rolling 30-day trust-calibration snapshot
(services/trust_calibration.py). Mirrors the existing
chat_ledger_reconciler supervised-loop convention (main.py).

A missed run (process restart, deploy) just means the next tick
recomputes the CURRENT window from scratch off the durable
`task_verifications` ledger — there is no incremental state to lose,
so recovery is automatic and exact."""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

INTERVAL_S = int(os.getenv("TRUST_CALIBRATION_INTERVAL_S", str(6 * 60 * 60)))  # 6h


async def trust_calibration_cron() -> None:
    """Long-lived background loop (registered via `supervise(...,
    long_lived=True)` in main.py, same convention as
    services/chat_reliability.chat_ledger_reconciler_loop)."""
    from cto_services.db import get_db
    while True:
        try:
            db = get_db()
            if db is not None:
                from services.trust_calibration import (
                    compute_correction_rate_30d, save_snapshot,
                )
                snap = await compute_correction_rate_30d(db)
                await save_snapshot(db, snap)
                logger.info(
                    "trust_calibration_cron: snapshot computed "
                    "(denominator=%s numerator=%s rate=%s)",
                    snap.get("denominator"), snap.get("numerator"),
                    snap.get("correction_rate_pct"),
                )
        except Exception as e:                               # noqa: BLE001
            logger.warning("trust_calibration_cron tick failed: %r", e)
        await asyncio.sleep(INTERVAL_S)
