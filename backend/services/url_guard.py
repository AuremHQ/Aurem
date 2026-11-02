"""
services/url_guard.py — Part 2c C8.

ONE shared SSRF guard for any code path that navigates/fetches a
user-controlled URL server-side. Generalizes the EXISTING guard in
services/url_fetcher.py::_is_safe_host (same core idea: reject
private/loopback/link-local/reserved/multicast via `ipaddress`, after
resolving DNS for a hostname) rather than reinventing it — this is
the promoted, reusable version, with two gaps closed that matter for
a BROWSER navigation (as opposed to url_fetcher's HTTP GET):

  1. Fail CLOSED on a DNS resolution error. `_is_safe_host` fails
     OPEN ("the request will fail anyway — let httpx handle it"),
     which is a reasonable call for an httpx GET that will just error
     out harmlessly, but wrong for a guard whose whole job is "deny
     by default on uncertainty."
  2. Unwraps IPv4-mapped IPv6 literals (`::ffff:127.0.0.1`) before
     the private/loopback check — Python's `ipaddress.IPv6Address.
     is_loopback` does NOT consider the embedded IPv4 address.
  3. Restricts to http/https only (no `file://`, `ftp://`, etc.) and
     rejects credentials embedded in the URL (`user:pass@host`).

Used by services/preview_capture.py::capture_screenshot (navigates
Playwright to a user-set `preview_url`) at THREE points: (a) when the
URL is saved (routers/cto_projects/management.py), (b) at capture
call time (DNS can change between save and capture), and (c) on every
Playwright request/redirect via route interception (catches a
server-side redirect to a private IP, which neither (a) nor (b) can
see in advance).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal"})


def _is_unsafe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def is_safe_public_http_url(url: str | None) -> bool:
    """True only for an http(s) URL with no embedded credentials whose
    host resolves EXCLUSIVELY to public, routable IP addresses. Fails
    CLOSED on any error, ambiguity, or malformed input — never raises."""
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:                                      # noqa: BLE001
        return False
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").strip()
    if not host:
        return False
    if host.lower() in _BLOCKED_HOSTNAMES:
        return False

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        return not _is_unsafe_ip(literal_ip)

    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:                                        # noqa: BLE001
        # Fail CLOSED — unlike url_fetcher._is_safe_host, an
        # unresolvable/errored lookup is denied, not passed through.
        return False
    if not infos:
        return False
    for info in infos:
        try:
            resolved_ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError, TypeError):
            return False
        if _is_unsafe_ip(resolved_ip):
            return False
    return True
