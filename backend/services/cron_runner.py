"""Shared scheduler-loop helpers for the cron fleet (W3 N2 / W4 m7).

Before this module, every cron module in ``backend/services`` carried its
own ~20-line copy of the same loop skeleton: an optional startup sleep, a
``while True`` loop, a ``try``/``except`` around one tick, an interval sleep,
and ``asyncio.CancelledError`` re-raised at every await so a cancelled task
reports as cancelled on shutdown (see the 2026-08-23 fix in
``daily_digest.py`` / ``billing_cron.py``).  The fifteen fleet modules now
run their loops through :func:`run_cron` (fixed interval) or
:func:`run_clock_cron` (sleep until a wall-clock time, then run once), and
:func:`register_cron` is the one-line way for new crons to join the fleet.

Sleep is always awaited through the ``asyncio`` module attribute
(``await asyncio.sleep(...)``), never through a name imported into this
module: tests patch ``asyncio.sleep`` on the shared module object (for
example ``test_iter_c2_probe_jitter.py`` monkeypatches
``integration_health_cron.asyncio.sleep``), and that patch only takes
effect if the sleep call resolves the attribute at await time.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

__all__ = ["run_cron", "run_clock_cron", "register_cron"]

# A tick is a zero-argument awaitable; an error handler takes the exception.
Tick = Callable[[], Awaitable[None]]
ErrorHandler = Callable[[BaseException], None]
WaitSeconds = Callable[[], float]


async def _sleep(seconds: float) -> None:
    """Sleep, letting ``asyncio.CancelledError`` propagate (never swallow it).

    A cancelled cron task must report as cancelled on shutdown instead of
    finishing in the "completed" state -- see the 2026-08-23 fix.
    """
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        raise


def _handle_error(name, exc, on_error, log):
    if on_error is not None:
        on_error(exc)
    else:
        log.warning("%s cron tick failed: %r", name, exc)


async def run_cron(
    name: str,
    interval_seconds: float,
    tick: Tick,
    *,
    startup_delay_seconds: float = 0,
    on_error: Optional[ErrorHandler] = None,
    log: Optional[logging.Logger] = None,
) -> None:
    """Run ``tick`` on a fixed interval, forever.

    Sleeps ``startup_delay_seconds`` first (boot stagger), then repeats:
    run one tick, sleep ``interval_seconds``.  A tick that raises is
    reported through ``on_error`` (or a warning log) and the loop carries
    on; ``asyncio.CancelledError`` always propagates so shutdown cancels
    the task cleanly.  This never returns under normal operation.
    """
    _log = log or logger
    await _sleep(startup_delay_seconds)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad tick must not kill the loop
            _handle_error(name, exc, on_error, _log)
        await _sleep(interval_seconds)


async def run_clock_cron(
    name: str,
    next_wait_seconds: WaitSeconds,
    tick: Tick,
    *,
    startup_delay_seconds: float = 0,
    backoff_seconds: float = 0,
    on_error: Optional[ErrorHandler] = None,
    log: Optional[logging.Logger] = None,
) -> None:
    """Run ``tick`` once per wall-clock slot, forever.

    Each cycle computes how long to wait via ``next_wait_seconds()``
    (for example "seconds until 06:00 UTC"), sleeps that long, then runs
    one tick.  The sleep and the tick share one ``try`` so a cancellation
    during either one re-raises instead of completing the task (the
    2026-08-23 fix).  A tick that raises is reported through
    ``on_error`` (or an exception log) and, when ``backoff_seconds`` is
    set, the loop waits that long before recomputing the next slot so a
    failing tick cannot tight-loop.
    """
    _log = log or logger
    await _sleep(startup_delay_seconds)
    while True:
        try:
            await _sleep(next_wait_seconds())
            await tick()
        except asyncio.CancelledError:
            # Re-raise so the task reports as genuinely cancelled on
            # shutdown instead of "completed normally" (2026-08-23 fix).
            raise
        except Exception as exc:  # noqa: BLE001 - one bad tick must not kill the loop
            if on_error is not None:
                on_error(exc)
            else:
                _log.exception("%s scheduler crash: %r", name, exc)
            if backoff_seconds:
                await _sleep(backoff_seconds)


def register_cron(
    name: str,
    interval_seconds: float,
    tick: Tick,
    *,
    startup_delay_seconds: float = 0,
    enabled: bool = True,
    on_error: Optional[ErrorHandler] = None,
    log: Optional[logging.Logger] = None,
) -> Optional[Awaitable[None]]:
    """Return the coroutine that runs a fixed-interval cron, or ``None``.

    This is the single entry point for scheduling a cron from ``main.py``
    (or any new module): ``asyncio.create_task(register_cron(...))``.
    ``enabled=False`` (typically an ``ENABLE_*`` env toggle) yields
    ``None`` so the caller simply skips creating the task.  Existing
    fleet modules keep their ``schedule_*_cron`` functions as thin
    wrappers around :func:`run_cron` / :func:`run_clock_cron` so their
    public names and signatures do not change.
    """
    if not enabled:
        return None
    return run_cron(
        name,
        interval_seconds,
        tick,
        startup_delay_seconds=startup_delay_seconds,
        on_error=on_error,
        log=log,
    )
