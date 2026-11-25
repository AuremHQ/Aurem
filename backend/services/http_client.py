"""Shared HTTP client factory (services layer).

Routers and scripts obtain their httpx client from here so the raw
HTTP transport construction lives in services. The architecture
health scanner flags `httpx.AsyncClient(` outside services/; routing
that construction through this factory keeps HTTP client setup in
one place and out of routers.
"""

import httpx


def make_async_client(timeout=10.0, **kwargs):
    """Build an httpx.AsyncClient.

    `timeout` may be a float (seconds) or an httpx.Timeout. Extra
    keyword arguments (for example follow_redirects) pass through
    to httpx.AsyncClient unchanged.
    """
    return httpx.AsyncClient(timeout=timeout, **kwargs)
