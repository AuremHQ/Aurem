"""
Part XIII · 2(a) — StartBody mode field (backend half of the mode
parameter) + the start_loop validation/wiring contract.

StartBody itself accepts the field; start_loop normalises it,
rejects anything outside swift/pro/maxx with 422 invalid_loop_mode
BEFORE any side effect, and threads the validated mode into
LoopEngine(mode=...). The live endpoint is not driven here (it needs
locks/wallet/rate-limit); the wiring is proven by the model test +
the source contract + the engine-level tests in
test_part13_ship_snapshot_pr.py.
"""
import os

import pytest
from pydantic import ValidationError

from routers.loop import StartBody


def test_startbody_accepts_mode():
    body = StartBody(project_id="p1", user_message="hi", mode="maxx")
    assert body.mode == "maxx"


def test_startbody_mode_defaults_to_none():
    body = StartBody(project_id="p1", user_message="hi")
    assert body.mode is None


def test_startbody_mode_too_long_rejected_by_model():
    with pytest.raises(ValidationError):
        StartBody(project_id="p1", user_message="hi", mode="x" * 17)


def test_start_loop_validates_mode_and_threads_it_into_engine():
    """Source contract for start_loop in routers/loop.py: normalise
    (strip/lower), 422 invalid_loop_mode outside swift/pro/maxx, and
    pass mode=_mode to LoopEngine."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "routers",
                            "loop.py")).read()
    assert '_mode = (body.mode or "").strip().lower() or None' in src
    assert '_mode not in ("swift", "pro", "maxx")' in src
    assert '"invalid_loop_mode"' in src
    assert "mode=_mode," in src
    # The 422 is raised before any side effect (lock/wallet/rate
    # limit) — the validation block sits above the kill-switch/lock
    # checks in the function body.
    assert src.index("invalid_loop_mode") < src.index("loop_mode_kill_switch")
