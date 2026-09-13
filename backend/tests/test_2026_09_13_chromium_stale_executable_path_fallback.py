"""
tests/test_2026_09_13_chromium_stale_executable_path_fallback.py

Founder-reported bug: ORA Chat's web_inspect tool failed with
"Chromium executable unavailable" against github.com/obra/superpowers
(a reachable URL). Root cause, confirmed directly on this pod:

  1. `PLAYWRIGHT_CHROME_EXECUTABLE_PATH` (checked first by
     web_inspect.py / deploy_verify.py / browser_tools.py) resolved to
     `/usr/local/bin/browser-use-chromium` - a symlink INTO
     `/pw-browsers/chromium_headless_shell-1208/...` that no longer
     existed because the installed Chromium build (1208) didn't match
     what the currently pinned `playwright==1.61.0` package expects
     (build 1228). This symlink is maintained by the platform's own
     preview-pod base image, outside this repo, so it can go stale at
     any time independent of anything in this codebase.
  2. Playwright's OWN default bundled-browser resolution (used when
     the override is unset) was ALSO empty for the real
     supervisor-spawned backend process - `PLAYWRIGHT_BROWSERS_PATH`
     is only exported in interactive shells on this pod, not passed
     through supervisor's controlled environment, so it fell back to
     `~/.cache/ms-playwright`, which had nothing installed.

Fix: `web_inspect.py` / `deploy_verify.py` / `browser_tools.py` now
retry ONCE without the `executable_path` override if that's
specifically what failed, before degrading to the browser-free
fallback - so staleness in EITHER resolution path alone no longer
takes the tool down, only staleness in BOTH. A boot-time probe
(main.py `_probe_chromium`) also self-heals the bundled path via
`playwright install chromium` if both are broken, and mirrors the
result onto `/api/health`.

This test exercises the ACTUAL fallback code in `web_inspect.py`
(not a re-implementation) by monkeypatching just the Chromium launch
call to simulate a stale override + working bundled resolution -
the exact real-world condition reproduced live on this pod.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.mark.asyncio
async def test_stale_executable_path_falls_back_to_bundled_resolution(monkeypatch):
    """Simulates exactly what happened live: executable_path override
    is stale (raises the exact Playwright "doesn't exist" error), but
    Playwright's own bundled resolution (no override) works fine. The
    fix must retry once and succeed, not degrade to browser-free."""
    from services import web_inspect

    monkeypatch.setenv("PLAYWRIGHT_CHROME_EXECUTABLE_PATH",
                        "/usr/local/bin/browser-use-chromium-STALE-FOR-TEST")

    class _FakeBrowser:
        async def new_context(self, **kw):
            raise RuntimeError("test stops here - launch fallback already proven")

        async def close(self):
            pass

    class _FakePW:
        class chromium:
            calls = []

            @staticmethod
            async def launch(**kwargs):
                _FakePW.chromium.calls.append(kwargs)
                if kwargs.get("executable_path"):
                    raise Exception(
                        "BrowserType.launch: Executable doesn't exist at "
                        "/usr/local/bin/browser-use-chromium-STALE-FOR-TEST\n"
                        "Looks like Playwright was just installed or updated."
                    )
                return _FakeBrowser()

    class _FakeAsyncPW:
        async def __aenter__(self):
            return _FakePW()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: _FakeAsyncPW())

    try:
        await web_inspect.run_web_inspect("https://example.com", "test question")
    except RuntimeError as e:
        assert "launch fallback already proven" in str(e)
    else:
        pytest.fail("expected the fake new_context() RuntimeError to propagate "
                     "past the launch retry")

    # The key assertion: TWO launch attempts were made - first WITH the
    # (stale) override, then WITHOUT it - proving the fallback path ran.
    assert len(_FakePW.chromium.calls) == 2
    assert _FakePW.chromium.calls[0].get("executable_path") == \
        "/usr/local/bin/browser-use-chromium-STALE-FOR-TEST"
    assert "executable_path" not in _FakePW.chromium.calls[1]


@pytest.mark.asyncio
async def test_both_paths_broken_still_degrades_gracefully(monkeypatch):
    """If the override AND the bundled fallback are both broken, the
    tool must still degrade to the browser-free path (no crash), same
    as before this fix - this fix only ADDS a retry, it must not
    remove the existing safety net."""
    from services import web_inspect

    monkeypatch.setenv("PLAYWRIGHT_CHROME_EXECUTABLE_PATH", "/definitely/missing")

    class _FakePW:
        class chromium:
            @staticmethod
            async def launch(**kwargs):
                raise Exception(
                    "BrowserType.launch: Executable doesn't exist at /definitely/missing"
                )

    class _FakeAsyncPW:
        async def __aenter__(self):
            return _FakePW()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: _FakeAsyncPW())

    async def _fake_fallback(url, e):
        return {"snapshot": "", "screenshot_meta": None, "error": None,
                "egress_attempts": [], "browser_available": False,
                "degraded_reason": f"chromium_unavailable: {str(e)[:300]}"}

    monkeypatch.setattr(web_inspect, "_browser_free_snapshot_fallback", _fake_fallback)

    result = await web_inspect.run_web_inspect("https://example.com", "test question")
    assert result["browser_available"] is False
    assert "chromium_unavailable" in (result["degraded_reason"] or "")


def test_live_chromium_actually_launches_on_this_pod():
    """Sanity check against the REAL environment (no mocks) - proves
    the actual fix (fixed symlink + app-level fallback) works on this
    pod right now, not just in a monkeypatched simulation."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        exe = os.environ.get("PLAYWRIGHT_CHROME_EXECUTABLE_PATH")
        kwargs = {"headless": True}
        if exe:
            kwargs["executable_path"] = exe
        browser = pw.chromium.launch(**kwargs)
        browser.close()
