"""
tests/test_2026_02_deployment_health_cron_cold_start_delay.py

Root cause (production deployment failure): `integration_health_cron`'s
`_PROBE_CACHE` is empty on every fresh process boot, so the FIRST cron
tick always runs the full, uncached, heaviest probe burst (Stripe's 8
sequential calls + a real E2B sandbox create/run/kill round-trip) —
already confirmed in integration_health.py's own "round 3" comment to
be the exact trigger nginx's `/health` 110-timeouts line up with. That
first tick fired at 150-210s post-boot (150s + 0-60s per-worker
jitter), which still falls inside a typical Kubernetes deployment's
readiness/liveness evaluation window — so a fresh rollout could see
this cold-start burst competing with the platform's own health probe
and get marked unhealthy, exactly matching a real deployment failure
log: `nginx ... upstream timed out (110) ... GET /health`.

Fix: push the first tick to 300-360s so it lands safely after that
window closes. This test locks the new value so it can't silently
regress back under 300s (the `>= 120` lock in test_iter336_boot_
stagger.py stays true but is too loose to catch that regression).
"""
import re
from pathlib import Path

CRON = Path("/app/backend/services/integration_health_cron.py").read_text(encoding="utf-8")


def test_first_tick_delay_is_at_least_300s():
    seg = CRON.split("# First probe:", 1)[1][:1200]
    m = re.search(r"sleep\((\d+(?:\.\d+)?)", seg)
    assert m, "no stagger sleep found after '# First probe:'"
    delay = float(m.group(1))
    assert delay >= 300, (
        f"integration_health_cron's first-tick delay regressed to {delay}s "
        "(must stay >= 300s so the cold-start uncached probe burst — "
        "Stripe's 8 calls + a real E2B round-trip — lands after a "
        "typical deployment readiness window closes)"
    )


def test_startup_jitter_still_applied_on_top_of_the_base_delay():
    seg = CRON.split("# First probe:", 1)[1][:1200]
    assert "_startup_jitter_s()" in seg.split("await asyncio.sleep")[1][:60]
