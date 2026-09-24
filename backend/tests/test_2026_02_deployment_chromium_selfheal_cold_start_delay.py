"""
tests/test_2026_02_deployment_chromium_selfheal_cold_start_delay.py

Root cause (production deployment failure, second incident): the
Chromium self-heal background task (`main.py::_probe_chromium`)
previously slept only 45s after boot before probing/reinstalling a
missing Chromium binary via `playwright install chromium` — a heavy,
sustained subprocess (network download + extraction). Production
evidence: with 2 uvicorn workers, this warning fired TWICE ~13.8s
apart (each worker independently waking from its own un-jittered 45s
sleep), while nginx's `/health` proxy (1s tolerance) timed out
repeatedly in that same window — the identical class of bug already
root-caused and fixed for `integration_health_cron` (150s -> 300s).
45s is far inside a typical deployment health-check window.

Fix: raised to 330s + the same PID-seeded `_startup_jitter_s()` used
by integration_health_cron (offset +30s from that cron's 300s base so
the two heaviest boot tasks don't land in the same instant; the
jitter also naturally de-duplicates the install across workers, since
a later-waking worker's own launch re-check will usually already
succeed off the first worker's install on the shared filesystem).

This test locks the new floor and the jitter call so it can't
silently regress back to an un-jittered 45s.
"""
from pathlib import Path

MAIN = Path("/app/backend/main.py").read_text(encoding="utf-8")
_SEG = MAIN.split("async def _probe_chromium():", 1)[1][:5000]


def test_chromium_selfheal_delay_floor_is_at_least_300s():
    import re
    m = re.search(r"await _asyncio\.sleep\((\d+(?:\.\d+)?)", _SEG)
    assert m, "no stagger sleep found inside _probe_chromium"
    delay = float(m.group(1))
    assert delay >= 300, (
        f"_probe_chromium's cold-start delay regressed to {delay}s (must "
        "stay >= 300s so the heavy `playwright install chromium` subprocess "
        "lands after a typical deployment readiness window closes)"
    )


def test_chromium_selfheal_uses_startup_jitter_to_desync_multi_worker_installs():
    sleep_line = _SEG.split("await _asyncio.sleep(", 1)[1][:80]
    assert "_startup_jitter_s()" in sleep_line, (
        "the sleep must include _startup_jitter_s() so a 2nd worker's "
        "independent copy of this task doesn't wake at the exact same "
        "instant and duplicate the ~150MB Chromium download/extract"
    )


def test_chromium_import_of_startup_jitter_is_present():
    import ast
    tree = ast.parse(MAIN)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "services.integration_health_cron":
            if any(alias.name == "_startup_jitter_s" for alias in node.names):
                found = True
    assert found, "main.py must import _startup_jitter_s from services.integration_health_cron"
