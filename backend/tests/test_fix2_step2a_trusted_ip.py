"""
FIX 2, step 2a (founder-ordered, 2026-10-04) — a trusted-end IP
extractor, side-by-side logging, no behavior change yet.

services/rate_limiter.py::client_ip_from_request() takes the FIRST
entry of X-Forwarded-For, which the caller fully controls (proven live
on Preview earlier: a made-up header changes the returned value
outright). This step adds client_ip_trusted_from_request(), which
reads from the TRUSTED end of the chain instead — the hops Preview's
own edge appends, which a caller cannot influence.

Evidence for the hop count (see FIX 2 step 2a report for the full
live-curl transcript): four real requests against Preview's
/api/aurem-dev/health/echo-ip, varying how many fake hops the caller
prepended, always ended in exactly the same two trailing hops after
the genuine client IP: a Cloudflare edge IP (varies per request) and a
fixed ingress/load-balancer IP (identical, 34.160.159.238, across all
four). Hence _TRUSTED_HOP_COUNT = 2.

Plain `pytest` — no LLM key, no network, no Mongo needed.
"""
from __future__ import annotations

from types import SimpleNamespace

from services.rate_limiter import (
    client_ip_from_request, client_ip_trusted_from_request,
)


def _fake_request(xff: str | None, socket_host: str = "10.0.0.1"):
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(
        headers=headers, client=SimpleNamespace(host=socket_host),
    )


def test_spoofed_header_does_not_change_the_trusted_value():
    """A caller prepending any number of fake hops must not move the
    trusted-end value away from the real client IP that our own edge
    actually saw."""
    real_client_ip = "35.193.209.116"
    cf_edge_ip = "104.22.62.82"
    fixed_lb_ip = "34.160.159.238"
    genuine_chain = f"{real_client_ip},{cf_edge_ip},{fixed_lb_ip}"

    baseline = client_ip_trusted_from_request(_fake_request(genuine_chain))
    assert baseline == real_client_ip

    for fake_prefix in ("9.9.9.9", "9.9.9.9, 8.8.8.8, 7.7.7.7",
                        "1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4, 5.5.5.5"):
        spoofed_chain = f"{fake_prefix},{genuine_chain}"
        req = _fake_request(spoofed_chain)
        naive = client_ip_from_request(req)
        trusted = client_ip_trusted_from_request(req)
        assert naive == fake_prefix.split(",")[0].strip(), (
            "sanity check: the naive function should still be fooled "
            "(that's the documented, unfixed gap) — if this fails, "
            "the test setup itself is wrong")
        assert trusted == real_client_ip, (
            f"trusted value moved under spoofing: prefix={fake_prefix!r} "
            f"-> trusted={trusted!r}, expected {real_client_ip!r}")


def test_normal_request_still_gets_its_real_ip():
    """A normal request (no spoofing attempt, exactly the shape
    Preview's own edge produces) must resolve to the real client IP
    via the trusted function — proves this isn't just "ignore
    everything", it correctly recovers the genuine value too."""
    real_client_ip = "52.161.69.171"
    chain = f"{real_client_ip},172.70.126.116,34.160.159.238"
    req = _fake_request(chain)
    assert client_ip_trusted_from_request(req) == real_client_ip
    assert client_ip_from_request(req) == real_client_ip  # naive agrees too, absent spoofing


def test_trusted_value_falls_back_gracefully_with_no_xff_header():
    """Direct/internal calls with no X-Forwarded-For at all (e.g. a
    health check hitting uvicorn directly) must not crash — both
    functions fall back to the raw socket peer."""
    req = _fake_request(xff=None, socket_host="127.0.0.1")
    assert client_ip_from_request(req) == "127.0.0.1"
    assert client_ip_trusted_from_request(req) == "127.0.0.1"


def test_trusted_value_falls_back_when_chain_shorter_than_expected():
    """Fewer hops than the expected trusted-hop-count (e.g. an
    internal call that skipped part of the edge chain) must not
    crash or return an empty string — best-effort fallback to the
    first entry, same shape as the naive function."""
    req = _fake_request(xff="10.1.1.1")  # only 1 hop, expected >= 3
    assert client_ip_trusted_from_request(req) == "10.1.1.1"
