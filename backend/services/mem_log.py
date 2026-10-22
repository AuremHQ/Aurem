"""services/mem_log.py — stdlib-only process RSS logger.

2026-10 follow-up to the ORA chat/fix reliability incident review:
the VPA-eviction RCA for 2026-10-21 22:30-23:00 UTC could not state
actual/peak process memory at the time (no metrics-server/Prometheus
tool available to the deployer agent). This gives the NEXT incident
real data to correlate against, with zero new dependencies — reads
Linux's /proc/self/status (current RSS, VmRSS) with a
resource.getrusage() fallback (peak RSS, ru_maxrss) for non-Linux.

No new service, no new dependency — stdlib only.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _rss_mb() -> float | None:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024, 1)
    except Exception:
        pass
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(kb / 1024, 1)
    except Exception:
        return None


def log_rss(tag: str) -> None:
    """Logs one line: `[mem] tag=<tag> rss_mb=<value>`. Never raises —
    a failed memory read must never break the boot/agent-run it's
    instrumenting."""
    mb = _rss_mb()
    logger.info("[mem] tag=%s rss_mb=%s", tag, mb if mb is not None else "unknown")
