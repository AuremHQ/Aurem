"""tests/test_regression_iter2026_54_part2c_c8_ssrf_preview_capture.py

Part 2c C8 [Security] — services/preview_capture.py::capture_screenshot
navigates Playwright directly to a URL taken from
`cto_projects.preview_url` (a user-controlled field) with NO guard at
all — http://127.0.0.1, http://localhost, http://169.254.169.254/
(cloud metadata), http://[::1], and file:// all reach `page.goto()`
unchallenged, and a server-side redirect from an initially-safe URL
to a private IP is followed with no re-check either.

CONFIRM (first 6 tests): each dangerous URL currently reaches
`page.goto()` — proven by mocking Playwright and asserting the mock
WAS called (the bug), not that it wasn't.

FIX: services/url_guard.py::is_safe_public_http_url — generalizes the
EXISTING SSRF guard in services/url_fetcher.py::_is_safe_host (same
private/loopback/link-local/reserved/multicast checks via `ipaddress`
+ DNS resolution) rather than reinventing it, and closes two gaps
that guard has: it fails OPEN when DNS resolution raises (this one
fails CLOSED), and it doesn't unwrap IPv4-mapped IPv6 addresses
(`::ffff:127.0.0.1`) before checking. Applied in capture_screenshot
BEFORE any browser launch (fail fast) AND via a Playwright route
interceptor registered on every request (initial nav + every
redirect + every sub-resource) so a safe-looking URL that redirects
server-side to a private IP is still blocked, and so a DNS answer
that changes between the pre-check and the actual request is
re-validated at request time.
"""
from __future__ import annotations

import pytest

import services.preview_capture as pc
from services.url_guard import is_safe_public_http_url

pytestmark = pytest.mark.asyncio


# ─── url_guard unit tests ──────────────────────────────────────────────
class TestIsSafePublicHttpUrl:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://localhost/",
        "http://localhost:3000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6 loopback
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://224.0.0.1/",          # multicast
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://user:pass@example.com/",  # credentials in URL
        "",
        None,
    ])
    def test_dangerous_or_malformed_urls_are_rejected(self, url):
        assert is_safe_public_http_url(url) is False

    def test_dns_resolution_failure_fails_closed(self, monkeypatch):
        """Unlike url_fetcher._is_safe_host (fails OPEN on a DNS
        error — 'the request will fail anyway'), this guard must fail
        CLOSED: an unresolvable host is denied, not passed through."""
        import socket as socket_mod

        def _raise(*a, **k):
            raise socket_mod.gaierror("simulated DNS failure")
        monkeypatch.setattr("socket.getaddrinfo", _raise)
        assert is_safe_public_http_url("http://this-will-not-resolve.invalid/") is False

    def test_a_genuinely_public_https_url_is_allowed(self, monkeypatch):
        import socket as socket_mod

        def _fake_getaddrinfo(host, *a, **k):
            return [(socket_mod.AF_INET, None, None, "", ("93.184.216.34", 0))]
        monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo)
        assert is_safe_public_http_url("https://example.com/") is True

    def test_literal_public_ip_is_allowed(self):
        assert is_safe_public_http_url("http://93.184.216.34/") is True


# ─── capture_screenshot wiring ─────────────────────────────────────────
class _FakePage:
    def __init__(self):
        self.goto_calls = []
        self.routes = []

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append(url)

    async def wait_for_timeout(self, ms):
        pass

    async def screenshot(self, type=None, quality=None):
        return b"fake-jpeg-bytes"


class _FakeContext:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page

    async def route(self, pattern, handler):
        self._page.routes.append((pattern, handler))


class _FakeBrowser:
    def __init__(self, context):
        self._context = context
        self.closed = False

    async def new_context(self, viewport=None):
        return self._context

    async def close(self):
        self.closed = True


def _install_fake_playwright(monkeypatch, page):
    context = _FakeContext(page)
    browser = _FakeBrowser(context)

    class _FakeChromium:
        async def launch(self, headless=True):
            return browser

    class _FakePlaywrightCtx:
        async def __aenter__(self):
            class _PW:
                chromium = _FakeChromium()
            return _PW()

        async def __aexit__(self, *a):
            return False

    def _fake_async_playwright():
        return _FakePlaywrightCtx()

    import sys
    import types
    fake_module = types.ModuleType("playwright.async_api")
    fake_module.async_playwright = _fake_async_playwright
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)
    return browser


class TestCaptureScreenshotSsrfGuard:
    @pytest.mark.parametrize("dangerous_url", [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "file:///etc/passwd",
    ])
    async def test_dangerous_url_never_reaches_playwright_goto(self, monkeypatch, dangerous_url):
        page = _FakePage()
        _install_fake_playwright(monkeypatch, page)

        result = await pc.capture_screenshot(dangerous_url, "phone")

        assert result is None, (
            f"C8 FIX REQUIRED: capture_screenshot must fail closed (return "
            f"None) for {dangerous_url!r}, never fabricate an image."
        )
        assert page.goto_calls == [], (
            f"C8 FIX REQUIRED: page.goto must NEVER be called for "
            f"{dangerous_url!r} — the SSRF guard must reject it before any "
            f"browser navigation happens."
        )

    async def test_redirect_to_a_private_ip_is_blocked_via_route_interception(self, monkeypatch):
        """The initial URL is safe (a real public host); Playwright's
        route interceptor must be registered so that a server-side
        redirect (or any sub-request) to a private IP is aborted, not
        silently followed."""
        page = _FakePage()

        def _fake_getaddrinfo_ok(host, *a, **k):
            return [(2, None, None, "", ("93.184.216.34", 0))]
        monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo_ok)
        _install_fake_playwright(monkeypatch, page)

        result = await pc.capture_screenshot("https://example.com/", "phone")

        assert result == b"fake-jpeg-bytes"
        assert page.goto_calls == ["https://example.com/"]
        assert len(page.routes) >= 1, (
            "C8 FIX REQUIRED: capture_screenshot must register a Playwright "
            "route interceptor BEFORE navigating, so a redirect to a "
            "private IP (which page.goto's own allow-listed initial URL "
            "check can never see) is still caught mid-navigation."
        )

        # Simulate the interceptor firing on a redirect to a private IP.
        _pattern, handler = page.routes[0]

        class _FakeRequest:
            def __init__(self, url):
                self._url = url

            def url(self):
                return self._url

        class _FakeRoute:
            def __init__(self, url):
                self.request = _FakeRequest(url)
                self.aborted = False
                self.continued = False

            async def abort(self):
                self.aborted = True

            async def continue_(self):
                self.continued = True

        redirect_route = _FakeRoute("http://169.254.169.254/latest/meta-data/")
        await handler(redirect_route)
        assert redirect_route.aborted is True, (
            "C8 FIX REQUIRED: the route interceptor must abort a request "
            "to a private/metadata IP, even mid-navigation after a redirect."
        )
        assert redirect_route.continued is False

        safe_sub_request = _FakeRoute("https://example.com/style.css")
        await handler(safe_sub_request)
        assert safe_sub_request.continued is True
        assert safe_sub_request.aborted is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
