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
    _LOG_SANITIZE_MAX_LEN, _sanitize_for_log, client_ip_from_request,
    client_ip_trusted_from_request,
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
    """FIX from this turn: with 0, 1, or 2 X-Forwarded-For entries
    (fewer than _TRUSTED_HOP_COUNT=2 requires to trust any position),
    the header cannot be trusted AT ALL — every entry in it could be
    caller-supplied. Must fall back to the raw SOCKET PEER address,
    never to anything the caller put in the header."""
    socket_host = "10.255.255.1"  # the pod's own internal peer IP, never attacker-reachable

    # 0 entries — header missing entirely.
    req0 = _fake_request(xff=None, socket_host=socket_host)
    assert client_ip_trusted_from_request(req0) == socket_host

    # 1 entry — a caller could have supplied this value outright.
    req1 = _fake_request(xff="9.9.9.9", socket_host=socket_host)
    trusted1 = client_ip_trusted_from_request(req1)
    assert trusted1 == socket_host, (
        f"expected fallback to socket peer {socket_host!r} with only "
        f"1 XFF entry, got {trusted1!r} (would have been attacker-"
        f"controlled if this fell back to the header instead)")

    # 2 entries — still not enough to trust (need > _TRUSTED_HOP_COUNT=2).
    req2 = _fake_request(xff="9.9.9.9, 8.8.8.8", socket_host=socket_host)
    trusted2 = client_ip_trusted_from_request(req2)
    assert trusted2 == socket_host, (
        f"expected fallback to socket peer {socket_host!r} with only "
        f"2 XFF entries, got {trusted2!r}")


def test_log_sanitizer_strips_control_characters_and_caps_length():
    """FIX from this turn: ip_naive is fully caller-controlled (that's
    the whole documented gap). A crafted X-Forwarded-For value must
    not be able to inject fake extra log lines (via \\n/\\r) or blow
    up log size."""
    malicious = "1.2.3.4\nFAKE LOG LINE: admin_login_success user=attacker\r\nmore evil"
    cleaned = _sanitize_for_log(malicious)
    assert "\n" not in cleaned and "\r" not in cleaned
    assert cleaned == "1.2.3.4FAKE LOG LINE: admin_login_success user=attackermore evil"[:_LOG_SANITIZE_MAX_LEN]

    huge = "9" * 10_000
    assert len(_sanitize_for_log(huge)) == _LOG_SANITIZE_MAX_LEN

    other_control_chars = "1.2.3.4\x00\x07\x1b[31mred\x7f"
    cleaned2 = _sanitize_for_log(other_control_chars)
    assert all(ord(c) >= 0x20 and ord(c) != 0x7f for c in cleaned2)


def test_client_ip_from_request_logs_sanitized_values(caplog):
    """End-to-end: client_ip_from_request() itself must not emit a
    raw, un-sanitized caller-controlled value into the log record."""
    import logging as _logging
    malicious_chain = "1.2.3.4\nINJECTED LINE,35.193.209.116,104.22.62.82,34.160.159.238"
    req = _fake_request(xff=malicious_chain)
    with caplog.at_level(_logging.INFO, logger="services.rate_limiter"):
        client_ip_from_request(req)
    logged_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "\nINJECTED LINE" not in logged_text, (
        "a crafted header value reached the log as a literal newline "
        "— log injection not actually prevented")
