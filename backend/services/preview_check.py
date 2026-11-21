"""services/preview_check.py — server-side preview reachability probe.

GETs a project's preview/live URL and classifies it best-effort:
  - "no_deployment"  — the connection itself failed (DNS/refused/TCP
    timeout) or the body says "deployment not found". Nothing appears
    to be listening at this address at all.
  - "deployment_down" — a server DID respond, just with a 5xx error
    (something is deployed and reachable, just currently erroring).
  - reachable=true for any response under 500 (even a 404) — a web
    server exists and answered; the iframe will render whatever that
    actually is, which is itself informative.

It used to live inside the route handler in
routers/cto_projects/preview.py, which put raw httpx calls in a router
— an architecture-health boundary violation (HTTP belongs in
services/). Moved verbatim 2026-11-20 (health-score architecture
fix); the route now only does auth + DB lookup + URL validation and
delegates the probe here.
"""
from __future__ import annotations

import httpx


async def probe_url_reachability(url: str) -> dict:
    """Probe `url` (server-side, no CORS/iframe blind spots) and return
    the reachability dict the preview panel renders."""
    import httpx
    timeout = httpx.Timeout(connect=4.0, read=4.0, write=4.0, pool=4.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "aurem-preview-check/1.0"},
        ) as c:
            async with c.stream("GET", url) as r:
                status_code = r.status_code
                # Peek at just the first chunk (not the whole body —
                # keeps this fast even for large pages) so we can
                # sniff for known "no deployment" platform signatures
                # below, regardless of status code.
                body_snippet = b""
                async for chunk in r.aiter_bytes(chunk_size=4096):
                    body_snippet = chunk
                    break
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # No response at any level — the connection itself never
        # succeeded (DNS failure / refused / nothing listening). The
        # strongest signal available that nothing is actually
        # deployed at this address.
        return {
            "ok": True, "reachable": False, "reason": "no_deployment",
            "detail": f"Couldn't connect to {url} — nothing appears to be "
                       "listening at this address.",
            "checked_url": url,
        }
    except httpx.TimeoutException:
        return {
            "ok": True, "reachable": False, "reason": "deployment_down",
            "detail": f"{url} did not respond within 4 seconds.",
            "checked_url": url,
        }
    except httpx.RequestError as e:
        return {
            "ok": True, "reachable": False, "reason": "deployment_down",
            "detail": f"Couldn't reach {url} ({type(e).__name__}).",
            "checked_url": url,
        }

    # 2026-09-17 bug fix (round 2) — founder-reported LIVE on the real
    # target site (aurem.live): it responds with HTTP 400 and the
    # literal body "Deployment not found" — a deterministic, well-
    # documented "nothing is actually deployed here" signal used by
    # several hosting platforms. Because 400 < 500, this was
    # previously misclassified as `reachable: true`, so the panel
    # correctly deferred to the iframe — which then rendered that
    # provider's own blank/minimal error page with zero Aurem-side
    # explanation (exactly the reported symptom). Sniffing the body
    # for these known phrases, regardless of status code, catches
    # this "technically responded, but nothing is deployed" class.
    body_text = body_snippet.decode("utf-8", errors="ignore").lower()
    NO_DEPLOYMENT_BODY_SIGNATURES = (
        "deployment not found",
        "this deployment cannot be found",
    )
    if any(sig in body_text for sig in NO_DEPLOYMENT_BODY_SIGNATURES):
        return {
            "ok": True, "reachable": False, "reason": "no_deployment",
            "detail": f"{url} responded with HTTP {status_code}, but the "
                       "response itself says no deployment exists there.",
            "checked_url": url, "status_code": status_code,
        }

    if status_code < 500:
        return {
            "ok": True, "reachable": True,
            "status_code": status_code, "checked_url": url,
        }
    return {
        "ok": True, "reachable": False, "reason": "deployment_down",
        "detail": f"{url} responded with HTTP {status_code}.",
        "checked_url": url, "status_code": status_code,
    }
