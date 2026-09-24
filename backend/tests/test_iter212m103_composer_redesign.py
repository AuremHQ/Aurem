"""
test_iter212m103_composer_redesign.py — Iter 212m-103

Static guards on the LoopModeToggle / LoopStepBar / StreamHealthPill
redesign. We don't render React here (Python-only test), but we lock
the source for the contract surface so a future refactor can't
silently drop the testids or the wiring patterns.
"""
from pathlib import Path


def _read(rel: str) -> str:
    return Path(f"/app/frontend/src/{rel}").read_text(encoding="utf-8")


def test_loop_mode_toggle_v0_pill_present():
    src = _read("components/LoopModeToggle.jsx")
    # Pill button shape, testid, both states
    assert 'data-testid="loop-mode-toggle"' in src
    assert "Loop on" in src and "Loop off" in src
    # data-loop-active is the contract attr ChatPanel uses to flip
    # the placeholder + Send-button text.
    assert "data-loop-active" in src
    # Brand color #FF6608 (orange) for the active pill
    assert "#FF6608" in src
    # Persisted via existing key — backward compat with older bundles.
    assert "ora_execution_mode" in src


def test_loop_step_bar_5_phase_v0_layout():
    src = _read("components/LoopStepBar.jsx")
    assert 'data-testid="loop-step-bar"' in src
    # All 5 phases present
    for label in ("PLAN", "EXECUTE", "VERIFY", "SCAN", "SHIP"):
        assert label in src, f"Phase label '{label}' missing from LoopStepBar"
    # Per-step testids the test harness uses to drive Loop UX assertions.
    # The JSX uses a template literal `loop-step-${s.key}` so we just
    # assert the prefix + STEPS keys are present.
    assert "loop-step-${s.key}" in src
    for key in ("plan", "execute", "verify", "security", "ship"):
        assert f'key: "{key}"' in src or f'"{key}"' in src
    # Step states (done/active/future/error) drive icon + color swap
    for state in ("done", "active", "future", "error"):
        assert state in src


def test_stream_health_pill_retry_now_wiring():
    src = _read("components/ChatPanel.jsx")
    # StreamHealthPill now exposes onRetry (Iter 212m-103) and
    # ChatPanel passes a handler that aborts the in-flight controller.
    assert "Retry now" in src
    assert 'data-testid="chat-stream-retry-now"' in src
    assert "abortRef.current?.abort()" in src
    # `Slow response` is the banner headline in the screenshot
    assert "Slow response" in src
    # `Reconnecting` is the variant for the active retry hop
    assert "Reconnecting" in src


def test_send_button_is_circular_pill():
    src = _read("components/ChatPanel.jsx")
    # The send button should be a 38x38 circular icon button per
    # Iter 212m-103 redesign. Stop button shares the same geometry.
    assert 'data-testid="chat-send"' in src
    assert 'data-testid="chat-stop"' in src
    # Width/height 38 + borderRadius 50% are the v0 spec.
    assert 'width: 38, height: 38' in src
    assert 'borderRadius: "50%"' in src


def test_loop_mode_toggle_lives_inside_toolbar():
    """Loop selection was merged into the header Swift/Pro/Maxx dropdown
    (ModeLoopPill) — founder decision, see git log ba491c8e. The composer
    must have ZERO standalone loop toggles, and the send button must
    still live inside the composer toolbar."""
    import re
    src = _read("components/ChatPanel.jsx")
    toolbar_idx = src.find('<div className="composer-toolbar">')
    send_idx    = src.find('data-testid="chat-send"')
    assert toolbar_idx != -1, "composer-toolbar opener not found"
    assert send_idx != -1,   "chat-send button not found"
    assert toolbar_idx < send_idx, "send button must render inside the toolbar"
    assert not re.search(r"<LoopModeToggle\b", src), (
        "standalone LoopModeToggle must NOT be mounted in the composer — "
        "loop selection lives in the header ModeLoopPill dropdown")
    assert "<ModeLoopPill" in src, "ModeLoopPill (header loop selector) must be mounted"
